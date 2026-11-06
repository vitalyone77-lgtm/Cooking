"""Двойные нажатия: бот (middleware) и веб (лимит не списывается дублем)."""
import asyncio, os, sys
sys.path.insert(0, "/tmp/ct"); os.chdir("/tmp/ct")
os.environ["WEB_DATA_DIR"] = "/tmp/ct/web_data_dbl"
fails = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c: fails.append(n)

from tg_safety import OneHeavyAtATime

class Chat: id = 42
class Msg: chat = Chat()
class CB:
    def __init__(self, data): self.data = data; self.message = Msg(); self.answers = []
    async def answer(self, t=None, **k): self.answers.append(t)

async def bot_test():
    mw = OneHeavyAtATime(); runs = []
    async def handler(ev, data):
        runs.append(ev.data); await asyncio.sleep(0.3); return "ok"
    a, b, c = CB("confirm:go"), CB("confirm:go"), CB("tmr:1:2")
    await asyncio.gather(mw(handler, a, {}), mw(handler, b, {}), mw(handler, c, {}))
    check("бот: двойной тап «Подобрать» — одна генерация", runs.count("confirm:go") == 1, str(runs))
    check("бот: второму тапу ответ «уже готовлю»", any(x and "Уже готовлю" in x for x in b.answers), str(b.answers))
    check("бот: лёгкие кнопки (таймер) не блокируются", "tmr:1:2" in runs)
    await mw(handler, CB("confirm:go"), {})
    check("бот: после окончания можно снова", runs.count("confirm:go") == 2)
    async def boom(ev, data): raise RuntimeError("x")
    try: await mw(boom, CB("miss:go:r"), {})
    except RuntimeError: pass
    await mw(handler, CB("miss:go:r"), {})
    check("бот: после ошибки блокировка снимается", runs.count("miss:go:r") == 1)
asyncio.run(bot_test())

from fastapi.testclient import TestClient
import web_api
async def slow(*a, **k):
    await asyncio.sleep(2); return "🍽 Тест\n\nПродукты:\n- яйцо — 2 шт\n\nИнструкция:\n1. Свари.\nПРОДУКТЫ_СПИСОК: яйцо"
async def fake_search(d): return "q", []
web_api.generate_recipe = slow; web_api.search_recipes = fake_search
with TestClient(web_api.app) as cl:
    dev = cl.post("/api/device").json()["device"]; h = {"X-Device": dev}
    body = {"cuisine": "classic", "appliance": ["stove"], "time": "30", "servings": 1}
    r1 = cl.post("/api/recipe", json=body, headers=h); r2 = cl.post("/api/recipe", json=body, headers=h)
    check("веб: дубль отклонён", r1.status_code == 200 and r2.status_code == 429, f"{r1.status_code} {r2.status_code}")
    dev_id = web_api._dev(dev)
    check("веб: дубль не списал попытку", web_api.web_db.count_since(dev_id, "recipe", 0) == 1,
          str(web_api.web_db.count_since(dev_id, "recipe", 0)))
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
