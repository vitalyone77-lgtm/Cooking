"""Аудит 15.11.2026 (внешняя проверка): вход по чужой ссылке, ВК, партнёрская доля, аллергены, лимиты ИИ, гонки двух процессов,
объединение аккаунтов, копии, анкеты после перезапуска, количества и упаковки, админка, магазины, .env."""
import asyncio
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime as dt
from pathlib import Path

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_a2.db", "usage_a2.db-wal", "usage_a2.db-shm", "accounts_a2.db", "accounts_a2.db-wal", "accounts_a2.db-shm"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_a2.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_a2.db"
os.environ["WEB_DATA_DIR"] = "web_data_a2"
import shutil
shutil.rmtree(PROJ + "/web_data_a2", ignore_errors=True)

import accounts
import config
import plans
import profile_ctx as pc
import usage

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


# ---- 1. ссылка входа ----
VK = accounts.VK_OFFSET + 42
accounts._q("INSERT INTO links(dev_id, tg_id, ts) VALUES(?,?,?)", (9001, VK, time.time()))
accounts.apply_payment("pw:vk1", VK, 290, "RUB", 30, "month")
code = accounts.new_login_code(555)
try:
    accounts.claim_login_code(code, 9001)
    ok = False
except accounts.OtherAccount:
    ok = True
check("1: ссылка входа на устройстве с другим аккаунтом — отказ", ok and accounts.tg_of_device(9001) == VK)
check("1: подписка ВК не переехала к чужому", accounts.premium_until(VK) > time.time() + 29 * 86400 and accounts.premium_until(555) == 0)
code2 = accounts.new_login_code(555)
check("1: на чистом устройстве вход по ссылке работает", accounts.claim_login_code(code2, 9002) == 555 and accounts.tg_of_device(9002) == 555)

# ---- 3. партнёр и друзья: только платежи после перехода ----
import partners
partners.ensure("blogA")
OLD = accounts.VK_OFFSET + 77
accounts.apply_payment("pw:old1", OLD, 2900, "RUB", 365, "year")
accounts._q("UPDATE payments SET ts=? WHERE charge_id='pw:old1'", (time.time() - 200 * 86400,))
accounts.set_ref(OLD, "blogA")
st = partners.stats(partners.get("blogA"))
check("3: старый платёж давнего пользователя партнёру не засчитан", st["gross"] == 0 and st["due"] == 0, str(st)[:200])
accounts.apply_payment("pw:new1", OLD, 290, "RUB", 30, "month")
check("3: платёж после перехода засчитан", partners.stats(partners.get("blogA"))["gross"] == 290)

# ---- 4. аллергены ----
cases = [("глютен", "- Мука пшеничная — 200 г", True), ("глютен", "- Рисовая мука — 100 г", False), ("рыба", "- Лосось — 300 г", True),
         ("молоко", "- Сливки — 100 мл", True), ("молоко", "- Сыр (исключите при аллергии) — 50 г", True), ("молоко", "Исключено: молоко", False),
         ("свинина", "- Бекон — 50 г", True), ("мясо", "- Куриное филе — 300 г", True), ("глютен", "- Соевый соус — 1 ст. л.", True),
         ("корица", "🔄 Замена: корица → можно без неё", False), ("рыба", "Подавайте без рыбы", False)]
bad = []
for avoid, text, exp in cases:
    pc.base.set("")
    pc.set_avoid(avoid)
    if bool(pc.hits(text)) != exp:
        bad.append((avoid, text))
check("4: семейства аллергенов и служебные строки", not bad, str(bad))
import book
R = ("🍽 Сырники\n\n⏱ Время: 20 мин | 👥 Порций: 2 | 🔥 Способ: Плита\n\n*Пищевая ценность (на 1 порцию):*\nКалорийность: 350 ккал | Б: 20 г | Ж: 12 г | У: 40 г\n\n"
     "*Продукты:*\n- Творог — 400 г\n- Мука пшеничная — 60 г\n- Яйцо — 1 шт\n\n*Инструкция:*\n1. Смешайте творог, яйцо и муку.\n2. Сформуйте сырники.\n3. Обжарьте по 3 минуты с каждой стороны.")
D = {"cuisine": "classic", "time": "Не важно", "servings": 2, "portion": "normal", "appliance": ["stove"]}
book.add(D, "Сырники", R, ["творог"])
check("4: без исключений рецепт берётся из книги", book.find(7001, dict(D)) is not None)
check("4: с исключениями книга не используется (рецепт составит ИИ с запретом)", book.find(7002, dict(D, excluded="глютен")) is None)
pc.current.set(None)

# ---- 5. «забытые» продукты ----
import shopping
pc.set_avoid("лук")
R5 = "🍽 Суп\n\n*Продукты:*\n- Курица — 300 г\n\n*Инструкция:*\n1. Лук не используем, варите курицу.\n2. Добавьте чеснок.\n3. Подавайте без масла."
added = shopping.add_forgotten_ingredients(R5)[1]
check("5: исключённый и «не используем» не возвращаются в покупки", "лук репчатый" not in added and "масло растительное" not in added and "чеснок" in added, str(added))
pc.current.set(None)

# ---- 7. вопрос к ИИ в дневнике ----
import food_ai
calls = []


async def llm(messages, max_tokens=0):
    calls.append(1)
    if llm.mode == "fail":
        raise RuntimeError("deepseek: 500")
    await asyncio.sleep(0.05)
    return "не понимаю"


llm.mode = "junk"
food_ai.ai.call_llm_with_fallback = llm
U7 = 7007
before = (accounts.sess_get(food_ai._used_key(U7)) or {}).get("n", 0)
asyncio.run(food_ai.estimate(U7, "абракадабра хрень"))
check("7: бессмысленный ответ — попытка засчитана", (accounts.sess_get(food_ai._used_key(U7)) or {}).get("n", 0) == before + 1)
llm.mode = "fail"
asyncio.run(food_ai.estimate(U7, "ещё что-то странное"))
check("7: ИИ не ответил — попытка возвращена", (accounts.sess_get(food_ai._used_key(U7)) or {}).get("n", 0) == before + 1)
llm.mode = "junk"
calls.clear()
lim = food_ai.FREE_LIMIT if plans.plan_of(U7) in ("free", "anon") else food_ai.DAILY_LIMIT
accounts.sess_set(food_ai._used_key(U7), {"n": lim - 1})


async def burst():
    return await asyncio.gather(*[food_ai.estimate(U7, f"блюдо номер {i}") for i in range(6)])


asyncio.run(burst())
check("7: пачка одновременных вопросов — к ИИ ушёл один (остался 1 из лимита)", len(calls) == 1, str(len(calls)))
check("7: вызов виден в общем дневном счётчике", usage._count("SELECT COUNT(*) FROM gens WHERE kind='foodai'") >= 2)

# ---- 9. день плана: лишний разделитель и расход без разделителя ----
import week_menu
W = 990077
week_menu._save(W, {"base_data": {}, "meal_keys": ["breakfast"], "days_total": 2, "inventory": {"яйца": 10}, "inventory_initial": {"яйца": 10},
                    "basket_text": "к", "history_titles": [], "days": {}, "current_day": 1, "progress_index": 0})


async def no_search(meal_keys, base):
    return [{"meal_key": k, "label": k, "search": ""} for k in meal_keys]


async def llm9(messages, max_tokens=0):
    return ("===СЛЕДУЮЩЕЕ_БЛЮДО===\n🍽 Омлет\n\n*Продукты:*\n- Яйца — 2 шт\n\n*Инструкция:*\n1. Жарьте.\n===СЛЕДУЮЩЕЕ_БЛЮДО===\n\n"
            "ИСПОЛЬЗОВАНО_БАЗОВЫЕ: яйца=3")


week_menu.day_menu.prepare_meals_info = no_search
week_menu.build_day_menu_user_prompt = lambda i, b: "м"
week_menu.call_llm_with_fallback = llm9
try:
    slot = asyncio.run(week_menu.ensure_slot(W, 0))
    ok = slot["meal"]["title"] == "Омлет" and week_menu._load(W)["inventory"]["яйца"] == 7
except Exception as e:  # noqa: BLE001
    ok, slot = False, e
check("9: лишние разделители не роняют оплаченный день, расход вычтен", ok, str(slot)[:200])

# ---- 10 и 35. два процесса одновременно ----
script = f"""
import os, sys, time
os.chdir({PROJ!r}); sys.path.insert(0, {PROJ!r})
import accounts, usage, plans
kind = sys.argv[1]
if kind == "days":
    for _ in range(100):
        accounts.grant_days(7010, 1, "promo:X")
else:
    n = 0
    for _ in range(20):
        rid, why = usage.try_spend("7011", "recipe", "bot")
        n += bool(rid)
    print(n)
"""
with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
    f.write(script)
env = dict(os.environ)
ps = [subprocess.Popen([sys.executable, f.name, "days"], env=env) for _ in range(2)]
[p.wait() for p in ps]
days = (accounts.premium_until(7010) - time.time()) / 86400
check("10: два процесса начисляют дни одновременно — ни один день не потерян", 199.5 < days < 200.5, f"{days:.1f}")
config.PAYWALL_ENABLED = True
accounts.settings_set({"LIMITS_FREE": "recipe=5/1", "TRIAL_DAYS": 0}, "test")
plans.reload()
script2 = script.replace("import accounts, usage, plans", "import accounts, usage, plans, config\nconfig.PAYWALL_ENABLED = True\nplans.reload()")
with open(f.name, "w") as g:
    g.write(script2)
ps = [subprocess.Popen([sys.executable, f.name, "spend"], env=env, stdout=subprocess.PIPE, text=True) for _ in range(2)]
got = sum(int((p.communicate()[0] or "0").strip().splitlines()[-1]) for p in ps)
os.unlink(f.name)
lim7011 = plans.limit("7011", "recipe")
check("35: бот и сайт одновременно — подборов ровно по лимиту", lim7011 and got == lim7011[0], f"{got} при лимите {lim7011}")
config.PAYWALL_ENABLED = False

# ---- 11. объединение: оплаченное время складывается ----
TG, VK2 = 7020, accounts.VK_OFFSET + 7020
accounts.apply_payment("pw:m1", TG, 290, "RUB", 30, "month")
accounts.apply_payment("pw:m2", VK2, 290, "RUB", 30, "month")
c = accounts._conn()
with c:
    accounts._merge_accounts(c, str(VK2), str(TG))
d = (accounts.premium_until(TG) - time.time()) / 86400
check("11: месяц + месяц при объединении = 60 дней", 59.5 < d < 60.5, f"{d:.1f}")

# ---- 12. копия ----
import backup
# Windows: sqlite-файл может быть ещё занят при выходе из TemporaryDirectory — не роняем тест из-за очистки.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
    folder, errs = backup.run(Path(td))
    names = {p.name for p in folder.iterdir()}
check("12: копия: все базы и файлы, без ошибок", not errs and {"accounts_a2.db", "usage_a2.db", "web.db"} <= names, f"{errs} {names}")

# ---- 13. анкета переживает перезапуск ----
from aiogram.fsm.storage.base import StorageKey
import fsm_store
key = StorageKey(bot_id=1, chat_id=7030, user_id=7030)
s1 = fsm_store.SqliteStorage()
asyncio.run(s1.set_state(key, "RecipeForm:excluded"))
asyncio.run(s1.set_data(key, {"cuisine": "classic", "appliance": {"stove"}}))
s2 = fsm_store.SqliteStorage()          # «после перезапуска»
check("13: анкета и её данные после перезапуска на месте", asyncio.run(s2.get_state(key)) == "RecipeForm:excluded"
      and asyncio.run(s2.get_data(key)) == {"cuisine": "classic", "appliance": {"stove"}})
asyncio.run(s2.set_state(key, None))
asyncio.run(s2.set_data(key, {}))
check("13: очищенная анкета удаляется из базы", not accounts._q("SELECT 1 FROM fsm WHERE key LIKE '1:7030:%'", one=True))

# ---- 14. расписание при выключенных напоминаниях ----
import reminders


async def sched():
    config.REMINDER_ENABLED = False
    s = reminders.setup_scheduler(object(), object())
    ids = {j.id for j in s.get_jobs()}
    s.shutdown(wait=False)
    config.REMINDER_ENABLED = True
    return ids


ids = asyncio.run(sched())
check("14: без напоминаний остальное расписание работает", "daily_reminder" not in ids and {"admin_alerts", "friend_rewards", "weekly_digest"} <= ids, str(ids))

# ---- 23–25. количества и упаковки ----
from stores import units, optimizer
from stores.base import Offer
check("23: «сливочное масло», «лимонный сок» — не штуки", not units.is_pieces("сливочное масло", 20) and not units.is_pieces("лимонный сок", 30)
      and units.is_pieces("сливы", 6) and units.is_pieces("лимон", 1))
lines = optimizer.choose_packs("яйца", 20, [Offer("vv", "Яйца 6", 120, 6), Offer("vv", "Яйца 10", 180, 10)])
check("24: 20 яиц — хватает и дешевле (2 по 10)", [(l.offer.pack_g, l.count) for l in lines] == [(10, 2)], str([(l.offer.pack_g, l.count) for l in lines]))
check("25: «1 200», «;» и «:»", shopping._parse_kv_amounts("курица=1 200; рис=300, яйца: 6 шт") == {"курица": 1200, "рис": 300, "яйца": 6})
check("25: «яйца — 2 шт (≈110 г)» это 2 шт, лук с граммами из скобок",
      shopping.amounts_from_recipe_text("*Продукты:*\n- Яйца — 2 шт (≈110 г)\n- Лук — 1 шт (≈100 г)\n\n*Инструкция:*\n1. x") == {"яйца": 2, "лук": 100})
check("25: «соль» не находит «фасоль»", shopping._find_amount("соль", {"фасоль": 200}) is None)

# ---- 29, 30. сайт ----
sw = open("web/sw.js", encoding="utf-8").read()
idx = open("web/index.html", encoding="utf-8").read()
check("29: офлайн-копия — только главная", 'if (url.pathname !== "/") return;' in sw)
check("30: фото продуктов и полки сжимаются перед отправкой", idx.count("lbShrink(file0)") == 2)

# ---- 31, 40, 43. сайт и админка ----
import web_api
import web_db
from fastapi.testclient import TestClient
cl = TestClient(web_api.app, base_url="https://testserver", follow_redirects=False)
with web_db.db() as c:
    c.execute("INSERT INTO ads(slot,title,image,url,active) VALUES('top','t','x.png','https://example.com',1)")
    ad = c.execute("SELECT MAX(id) FROM ads").fetchone()[0]
for _ in range(20):
    cl.get(f"/go/{ad}")
    cl.get("/api/ad?slot=top")
with web_db.db() as c:
    clicks = c.execute("SELECT COUNT(*) FROM ad_events WHERE ad_id=? AND kind='click'", (ad,)).fetchone()[0]
    views = c.execute("SELECT COUNT(*) FROM ad_events WHERE ad_id=? AND kind='view'", (ad,)).fetchone()[0]
check("31: 20 кликов и показов с одного адреса — по одному", clicks == 1 and views == 1, f"{clicks} {views}")
config.ADMIN_PASSWORD, config.WEB_SECRET = "pw-test-123", config.WEB_SECRET or "secret-test"
r = cl.post("/admin/login", json={"password": "pw-test-123"})
adm = r.cookies.get("adm") or cl.cookies.get("adm")
check("40: вход в админку — своя сессия", r.status_code == 200 and adm and adm.count(".") == 2 and cl.get("/admin/api/stats").status_code == 200, r.text[:100])
cl.post("/admin/logout")
cl2 = TestClient(web_api.app, base_url="https://testserver")
check("40: после «Выйти» тот же код не работает", cl2.get("/admin/api/stats", cookies={"adm": adm}).status_code == 401)
dev = cl.post("/api/device").json()["device"]
did = web_db.device_id(dev)
with web_db.db() as c:
    ls1 = c.execute("SELECT last_seen FROM devices WHERE id=?", (did,)).fetchone()[0]
time.sleep(0.05)
web_db.device_id(dev)
with web_db.db() as c:
    ls2 = c.execute("SELECT last_seen FROM devices WHERE id=?", (did,)).fetchone()[0]
check("43: «последний визит» не пишется на каждый запрос", ls1 == ls2)
r = cl.get("/api/meta")
check("рекомендация: ответы /api не кэшируются браузером, безопасные заголовки", r.headers.get("cache-control") == "no-store"
      and r.headers.get("x-content-type-options") == "nosniff")

# ---- 36, 37, 41, 42 ----
import storage
storage.save_last_request(7040, {"cuisine": "classic", "excluded": "арахис", "macro_goal": "1800"}, "x")
accounts.delete_my_data(7040)
lr = storage.get_last_request(7040)
check("36: «Удалить мои данные» стирает и исключения из анкеты", lr.get("excluded") == "" and lr.get("macro_goal") == "")
config.PAYWALL_ENABLED = True
accounts.grant_days(7041, 10, "promo:Y")
check("37: у подписки по промокоду пробная неделя не сгорает", plans.maybe_start_trial(7041) is None and not accounts.trial_used(7041))
config.PAYWALL_ENABLED = False
from stores import prefs
before = prefs.get_enabled(7042)
prefs.toggle(7042, before[0])
check("41: выбор магазинов в общей базе", accounts._q("SELECT stores FROM store_prefs WHERE uid='7042'", one=True) is not None
      and before[0] not in prefs.get_enabled(7042))
r = subprocess.run([sys.executable, "-c", "import config; print(config.REMINDER_HOUR, config.STAR_USD)"],
                   env=dict(os.environ, REMINDER_HOUR="14  # днём", STAR_USD=""), capture_output=True, text=True, cwd=PROJ)
check("42: опечатки в .env не роняют запуск", r.returncode == 0 and r.stdout.split()[:2] == ["14", "0.013"], r.stderr[-300:])

print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
