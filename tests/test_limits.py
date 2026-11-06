"""Лимиты бота, общий дневной потолок, возврат попытки при сбое, напоминания только активным."""
import asyncio, os, sys, time
sys.path.insert(0, "/tmp/ct"); os.chdir("/tmp/ct")
os.environ["USAGE_DB"] = "/tmp/ct/usage_test.db"
os.environ["WEB_DATA_DIR"] = "/tmp/ct/web_data_lim"
os.environ["ACCOUNTS_DB"] = "/tmp/ct/accounts_lim.db"
for p in ("/tmp/ct/usage_test.db", "/tmp/ct/accounts_lim.db"):
    if os.path.exists(p): os.remove(p)
fails = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c: fails.append(n)

import config, usage

# --- лимиты бота на пользователя ---
ids = [usage.bot_try_spend(1, "recipe")[0] for _ in range(3)]
check("бот: 3 рецепта в день проходят", all(ids))
sid, why = usage.bot_try_spend(1, "recipe")
check("бот: 4-й рецепт отклонён", sid is None and "лимит" in why.lower(), why)
check("бот: у другого пользователя свой лимит", usage.bot_try_spend(2, "recipe")[0])
usage.unlog(ids[-1])
check("бот: сбой возвращает попытку", usage.bot_try_spend(1, "recipe")[0])
check("бот: меню на день 1 в сутки", usage.bot_try_spend(1, "daymenu")[0] and not usage.bot_try_spend(1, "daymenu")[0])
check("бот: план раз в 3 дня", usage.bot_try_spend(1, "week")[0] and "раз в 3 дн" in usage.bot_try_spend(1, "week")[1])

# --- общий потолок ---
config.DAILY_GEN_CAP = usage.total_today() + 2
usage.log("web", 5, "recipe")
check("потолок: ещё не достигнут", not usage.cap_reached())
usage.log("reminder", 6, "reminder")
check("потолок: достигнут", usage.cap_reached())
sid, why = usage.bot_try_spend(3, "recipe")
check("потолок: бот вежливо отказывает", sid is None and "завтра" in why, why)

from fastapi.testclient import TestClient
import web_api
with TestClient(web_api.app) as cl:
    dev = cl.post("/api/device").json()["device"]
    r = cl.post("/api/recipe", json={"cuisine": "classic", "appliance": ["stove"], "time": "30", "servings": 1}, headers={"X-Device": dev})
    check("потолок: сайт отвечает 429 с понятным текстом", r.status_code == 429 and "завтра" in r.json().get("detail", ""), r.text[:120])
config.DAILY_GEN_CAP = 0
check("потолок 0 = без потолка", not usage.cap_reached())

# сайт: подбор пишется в общий счётчик, сбой его возвращает
config.DAILY_GEN_CAP = 500
before = usage.total_today()
async def boom(*a, **k):
    await asyncio.sleep(1.5); raise RuntimeError("ИИ упал")
async def fake_search(d): return "q", []
web_api.generate_recipe = boom; web_api.search_recipes = fake_search
with TestClient(web_api.app) as cl:
    dev = cl.post("/api/device").json()["device"]; h = {"X-Device": dev}
    j = cl.post("/api/recipe", json={"cuisine": "classic", "appliance": ["stove"], "time": "30", "servings": 1}, headers=h).json()
    check("сайт: подбор учтён в общем счётчике", usage.total_today() == before + 1)
    for _ in range(40):
        s = cl.get("/api/job/" + j["job"], headers=h).json()
        if s["status"] != "running": break
        time.sleep(0.2)
    check("сайт: сбой ИИ вернул попытку и в общем счётчике", usage.total_today() == before, f"{usage.total_today()} vs {before}")

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
