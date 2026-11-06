"""Нагрузка: python test_load.py bot|web — два процесса одновременно на общих базах (как бот и сайт на сервере)."""
import asyncio
import os
import sys
import time
from datetime import datetime

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
os.environ["USAGE_DB"] = PROJ + "/usage_load.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_load.db"
os.environ["WEB_DATA_DIR"] = "web_data_load"
MODE = sys.argv[1]
N = 60
errors = []

import logging


class Catch(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR or "locked" in record.getMessage():
            errors.append(record.getMessage()[:200])
logging.getLogger().addHandler(Catch())

import config
config.DAILY_GEN_CAP = 100000
config.WEB_IP_DAILY_GENS = 0
config.DAILY_GEN_CAP_ANON = 0


async def fake_search(d):
    await asyncio.sleep(0.3)
    return "q", []

RECIPE = ("🍽 Каша\n\nПродукты:\n- Овсяные хлопья — 60 г\n- Масло подсолнечное — 10 мл\n\nИнструкция:\n1. Вари 5 мин.\n"
          "2. Подай.\n\nПРОДУКТЫ_СПИСОК: овсяные хлопья, масло подсолнечное")


async def fake_gen(data, q, r):
    await asyncio.sleep(1.0)
    return RECIPE


async def bot_load():
    from aiogram.client.bot import Bot
    from aiogram.client.session.base import BaseSession
    from aiogram.types import Update, CallbackQuery, Message, Chat, User
    import bot as botmod
    from aiogram.fsm.storage.base import StorageKey
    import usage
    botmod.search_recipes = fake_search
    botmod.generate_recipe = fake_gen
    config.WEB_LIMIT_RECIPE = 1000
    import plans
    config.LIMITS_OPEN = "recipe=1000/1,daymenu=1000/1,week=1000/1,missing=1000/1"

    class S(BaseSession):
        async def close(self):
            pass

        async def make_request(self, bot, method, timeout=None):
            await asyncio.sleep(0.02)     # сеть до Telegram
            if type(method).__name__ in ("SendMessage", "EditMessageText"):
                return Message(message_id=int(time.time() * 1000) % 10**9, date=datetime.now(),
                               chat=Chat(id=getattr(method, "chat_id", 1) or 1, type="private"), text=getattr(method, "text", "") or "x").as_(bot)
            return True

        async def stream_content(self, *a, **k):
            raise NotImplementedError

    b = Bot(token="123456:TEST-TOKEN-NOT-REAL-1234567890abcdef", session=S())
    n = [0]

    def cb(uid, data, msg_id=600, text=RECIPE):
        n[0] += 1
        u, c = User(id=uid, is_bot=False, first_name="T"), Chat(id=uid, type="private")
        return Update(update_id=n[0], callback_query=CallbackQuery(id=str(n[0]), from_user=u, chat_instance="x", data=data,
                      message=Message(message_id=msg_id, date=datetime.now(), chat=c, text=text)))

    async def user(uid):
        t0 = time.time()
        await botmod.dp.storage.set_state(key=StorageKey(bot_id=b.id, chat_id=uid, user_id=uid), state=botmod.RecipeForm.confirm)
        await botmod.dp.storage.set_data(key=StorageKey(bot_id=b.id, chat_id=uid, user_id=uid),
                                         data={"cuisine": "classic", "appliance": ["stove"], "time": "30", "servings": 1})
        for d in ("confirm:go", "profile:open", "sl:o:r", "sl:i:0", "sl:m:h", "sl:i:1", "ck:start", "pf:shelf"):
            await botmod.dp.feed_update(b, cb(uid, d))
        return time.time() - t0

    t = time.time()
    times = await asyncio.gather(*(user(880000000 + i) for i in range(N)))
    total = time.time() - t
    gens = usage._count("SELECT COUNT(*) FROM gens WHERE src='bot'")
    print(f"бот: {N} пользователей × 8 действий за {total:.1f} с, худший пользователь {max(times):.1f} с; подборов в базе: {gens}")
    return total, max(times)


async def web_load():
    import httpx
    import web_api
    web_api.search_recipes = fake_search
    web_api.generate_recipe = fake_gen
    import plans
    config.LIMITS_OPEN = "recipe=1000/1,daymenu=1000/1,week=1000/1,missing=1000/1"
    web_api.API_RATE_PER_MIN = 100000
    tr = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=tr, base_url="http://t") as cl:
        devs = [(await cl.post("/api/device", headers={"X-Real-IP": f"10.0.{i // 200}.{i % 200}"})).json()["device"] for i in range(100)]
        statuses = []

        async def one(d, i):
            h = {"X-Device": d, "X-Real-IP": f"10.0.{i // 200}.{i % 200}"}
            r = await cl.post("/api/recipe", json={"cuisine": "classic", "appliance": ["stove"], "time": "30", "servings": 1}, headers=h)
            statuses.append(r.status_code)
            if r.status_code != 200:
                return
            j = r.json()["job"]
            for _ in range(100):
                s = (await cl.get("/api/job/" + j, headers=h)).json()
                if s["status"] != "running":
                    statuses.append(s["status"])
                    break
                await asyncio.sleep(0.2)
            for path in ("/api/me", "/api/favorites", "/api/overview"):
                statuses.append((await cl.get(path, headers=h)).status_code)
            statuses.append((await cl.post("/api/favorites", json={"title": "t", "text": "x"}, headers=h)).status_code)

        t = time.time()
        await asyncio.gather(*(one(d, i) for i, d in enumerate(devs)))
        total = time.time() - t
    bad = [s for s in statuses if s not in (200, "done")]
    print(f"сайт: 100 устройств (подбор + опрос + профиль + избранное) за {total:.1f} с; ошибок: {len(bad)} {bad[:5]}")
    return total, bad


if MODE == "bot":
    total, worst = asyncio.run(bot_load())
    ok = not errors and total < 30
else:
    total, bad = asyncio.run(web_load())
    ok = not errors and not bad and total < 40
print("ошибки в логах:", errors[:5])
print("ИТОГ:", "ВСЁ ОК" if ok else "ПРОВАЛЕНО")
