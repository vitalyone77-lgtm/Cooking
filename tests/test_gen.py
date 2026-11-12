"""Надёжность генерации (аудит другой веткой 12.11.2026): обрезанный ответ ИИ, замена в меню на день,
проверка исключённых продуктов без ложных срабатываний, сопоставление остатков плана, лимит вопросов к ИИ."""
import asyncio
import os
import sys

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_gn.db", "accounts_gn.db", "accounts_gn.db-wal", "accounts_gn.db-shm"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_gn.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_gn.db"

import ai
import config
import profile_ctx as pc
import week_menu

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


# ---- исключения: нет ложных срабатываний на служебных словах ----
R = ("*Продукты:*\n- Курица 300 г\n- Сырое яйцо 1 шт\n- Рисовая мука 50 г\n*Инструкция:*\n"
     "1. Варите без крышки 20 мин. Подавайте без сыра.")
for avoid in ("молочные продукты", "без глютена", "всё острое", "морепродукты"):
    pc.base.set("")
    pc.set_avoid(avoid)
    check(f"«{avoid}»: заголовок «Продукты», «без крышки», «сырое яйцо», «без сыра» — не нарушение", pc.hits(R) == [], str(pc.hits(R)))
pc.set_avoid("курица и рыба")
check("многословный пункт ловится по любому слову", pc.hits("- Рыба 200 г") == ["курица и рыба"])
pc.set_avoid("молочные продукты")
check("категория «молочные»: сыр, сливки, творог", all(pc.hits(x) for x in ("- Сыр твёрдый 50 г", "- Сливки 100 мл", "творожная масса")))
pc.set_avoid("глютен")
check("категория «глютен»: пшеничная мука, спагетти", pc.hits("- Мука пшеничная 200 г") and pc.hits("- Спагетти 200 г"))
check("«глютен»: пшено не ловится (оно без глютена)", not pc.hits("- Пшено 100 г"))
pc.set_avoid("морепродукты")
check("категория «морепродукты»: креветки", pc.hits("- Креветки 200 г") == ["морепродукты"])
pc.set_avoid(", ".join(f"продукт номер {i}" for i in range(40)))
check("длинный список исключений не режется посреди пункта", pc.current.get()["never"].endswith(tuple(f"номер {i}" for i in range(40))))
pc.current.set(None)

# ---- остатки плана ----
inv = {"лук": 1, "рис": 1, "соль и перец": 1, "лук зеленый": 1}
check("остатки: «луковица» = лук", week_menu._match_inventory_key(inv, "луковица") == "лук")
check("остатки: ключ с «и» находится", week_menu._match_inventory_key(inv, "соль и перец черный") == "соль и перец")
check("остатки: случайное совпадение букв не считается", week_menu._match_inventory_key({"мин": 1}, "витамин") is None)

# ---- обрезанный ответ ИИ ----
calls = []


async def fake(provider, messages, max_tokens):
    calls.append((provider, max_tokens))
    if fake.mode == "cut_once" and len(calls) == 1:
        raise ai._Truncated(provider, "половина")
    if fake.mode == "cut_always":
        raise ai._Truncated(provider, f"половина {max_tokens}")
    return "целиком"


ai._call_provider = fake
config.LLM_PROVIDER = "deepseek"
fake.mode = "cut_once"
r = asyncio.run(ai._call_any([{"role": "user", "content": "x"}], 1000))
check("обрезал один раз: повтор у того же провайдера с запасом", r == "целиком" and calls == [("deepseek", 1000), ("deepseek", 1500)], str(calls))
calls.clear()
fake.mode = "cut_always"
r = asyncio.run(ai._call_any([{"role": "user", "content": "x"}], 1000))
check("обрезал дважды: отдаём лучший неполный ответ, к резервным не ходим", r == "половина 1500" and len(calls) == 2, str(calls))

# ---- замена в меню на день ----
import day_menu
import missing

entry = {"order": ["breakfast", "lunch"], "meals": {"breakfast": {"title": "Каша", "text": "Каша с молоком", "ok": True},
                                                     "lunch": {"title": "Суп", "text": "Суп с молоком", "ok": True}},
         "base_data": {}, "shopping_terms": []}
saved = []
day_menu._load_menu = lambda cid: entry
day_menu._save_menu = lambda cid, e: saved.append(e)


async def adapt(text, miss, excl):
    if adapt.fail_all or text.startswith("Суп"):
        raise RuntimeError("ИИ не ответил")
    return "🍽 Каша на воде\n\nКаша на воде", ["крупа"]


missing.adapt_recipe = adapt
adapt.fail_all = False
asyncio.run(missing.rebuild_day(1, ["молоко"]))
check("одно блюдо не переделалось — другое всё равно заменено", saved and saved[-1]["meals"]["breakfast"]["text"].startswith("🍽 Каша на воде"))
adapt.fail_all = True
saved.clear()
try:
    asyncio.run(missing.rebuild_day(1, ["молоко"]))
    ok = False
except RuntimeError:
    ok = True
check("ни одно не переделалось — ошибка, а не «заменил»", ok and not saved)

# ---- план на несколько дней: генерация дня не затирает изменения и не идёт дважды ----
import accounts

W = 990001
gen_calls = []
prompts_seen = []


async def no_search(meal_keys, base):
    return [{"meal_key": k, "label": k, "search": ""} for k in meal_keys]


week_menu.day_menu.prepare_meals_info = no_search
week_menu.build_day_menu_user_prompt = lambda info, base: "меню"


async def fake_llm(messages, max_tokens=0):
    gen_calls.append(1)
    prompts_seen.append(messages[-1]["content"])
    if fake_llm.during:
        fake_llm.during()
    await asyncio.sleep(0.3)
    return ("🍽 Омлет\n\n*Продукты:*\n- Яйца — 2 шт\n\n*Инструкция:*\n1. Жарьте.\n" + week_menu.DAY_MENU_BLOCK_DELIMITER
            + "\nИСПОЛЬЗОВАНО_БАЗОВЫЕ: яйца=4")


fake_llm.during = None
week_menu.call_llm_with_fallback = fake_llm


def new_plan():
    week_menu._save(W, {"base_data": {}, "meal_keys": ["breakfast"], "days_total": 3, "inventory": {"яйца": 10, "молоко": 500},
                        "inventory_initial": {"яйца": 10, "молоко": 500}, "basket_text": "корзина", "history_titles": [],
                        "days": {}, "current_day": 1, "progress_index": 0})


new_plan()
week_menu._save(W, {**week_menu._load(W), "days": {"1": {"order": ["breakfast"], "meals": {"breakfast": {"title": "Каша", "text": "Каша"}}}},
                    "current_day": 2, "progress_index": 0})


def during():
    week_menu.mark_done(W, 0)                       # человек отметил «приготовлено» на другом устройстве
    st = week_menu._load(W)
    st["inventory"].pop("молоко")                    # и сказал «нет молока»
    week_menu._save(W, st)


fake_llm.during = during
asyncio.run(week_menu.ensure_slot(W, 1))
st = week_menu._load(W)
check("пока готовился день: отметка «приготовлено» не потерялась", st["progress_index"] == 1, str(st["progress_index"]))
check("пока готовился день: убранный продукт не вернулся", "молоко" not in st["inventory"], str(st["inventory"]))
check("расход дня вычтен из свежих остатков", st["inventory"]["яйца"] == 6 and "2" in st["days"] and st["current_day"] == 3, str(st))

fake_llm.during = None
gen_calls.clear()


async def twice():
    return await asyncio.gather(week_menu.ensure_slot(W, 2), week_menu.ensure_slot(W, 2))


r = asyncio.run(twice())
check("день открыли дважды одновременно — ИИ спрошен один раз", len(gen_calls) == 1 and r[0]["meal"] == r[1]["meal"], str(len(gen_calls)))

new_plan()
fake_llm.during = lambda: week_menu._save(W, {**week_menu._load(W), "basket_text": "другая корзина"})
try:
    asyncio.run(week_menu.ensure_slot(W, 0))
    ok = False
except RuntimeError:
    ok = True
check("план пересобрали во время генерации — старый день в новый план не записан", ok and not week_menu._load(W)["days"])

fake_llm.during = None
week_menu._save(W, {"base_data": {}, "meal_keys": ["breakfast"], "days_total": 3, "inventory": {"яйца": 0},
                    "inventory_initial": {"яйца": 10}, "basket_text": "к", "history_titles": ["Омлет"],
                    "days": {"1": {"order": ["breakfast"], "meals": {"breakfast": {"title": "Омлет", "text": "x"}}}},
                    "current_day": 2, "progress_index": 1})
prompts_seen.clear()
asyncio.run(week_menu.ensure_slot(W, 1))
check("корзина израсходована: ИИ не говорят «это первый день»", prompts_seen and "первый день" not in prompts_seen[-1]
      and "израсходованы" in prompts_seen[-1])
accounts.sess_set(f"wkgen:{W}", {"day": 0, "ts": 0})

# ---- разбор служебных строк плана (12.11 сломался: два правила с одним именем, план не создавался) ----
import shopping

check("корзина плана: числа без единиц, жирный маркер, кг", shopping.extract_base_products("Корзина\n**БАЗОВЫЕ_ПРОДУКТЫ:** курица=500, рис=1,5 кг, яйца=10 шт")[1]
      == {"курица": 500, "рис": 1500, "яйца": 10}, str(shopping.extract_base_products("БАЗОВЫЕ_ПРОДУКТЫ: курица=500")))
check("расход дня: «яйца=4»", shopping.extract_used_base_products("ИСПОЛЬЗОВАНО_БАЗОВЫЕ: яйца=4, молоко=200") == {"яйца": 4, "молоко": 200})
check("«соль» не считается уже указанной из-за «фасоль»", not shopping._same_item("соль", "фасоль") and shopping._same_item("масло", "масло сливочное"))


async def basket_llm(messages, max_tokens=0):
    return "🛒 Корзина на 3 дня\n- Курица 1,5 кг\n\nБАЗОВЫЕ_ПРОДУКТЫ: курица=1500, рис=600"


week_menu.call_llm_with_fallback = basket_llm
try:
    asyncio.run(week_menu.start_week_plan(W + 1, ["lunch"], 3, {}))
    st = week_menu._load(W + 1)
    ok = st["inventory"] == {"курица": 1500, "рис": 600} and "БАЗОВЫЕ" not in st["basket_text"]
except Exception as e:  # noqa: BLE001
    ok, st = False, e
check("план на несколько дней создаётся из ответа ИИ целиком", ok, str(st)[:200])

print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
