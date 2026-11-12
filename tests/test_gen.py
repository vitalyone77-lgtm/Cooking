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

print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
