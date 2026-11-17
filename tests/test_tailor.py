"""«🎯 Под меня»: рецепт из книги под исключения, цель и пожелания. Подписка; без неё — понятное предложение.
Бот и сайт; попытка списывается как замена продуктов и возвращается при сбое ИИ."""
import asyncio
import os
import sys
import time
from datetime import datetime as dt

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_tl.db", "accounts_tl.db", "accounts_tl.db-wal", "accounts_tl.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_tl.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_tl.db"
os.environ["WEB_DATA_DIR"] = "web_data_tl"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from fastapi.testclient import TestClient

import tg_safety
tg_safety.FloodGuard.__init__.__defaults__ = (100000, 10)
import accounts
import book
import bot as botmod
import config
import plans
import tailor
import usage
import web_api

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


config.PAYWALL_ENABLED = True
accounts.settings_set({"PREMIUM_FEATURES": ["memory", "pdf", "cart", "noads", "tailor"]}, "test")
plans.reload()
R = ("🍽 Паста с грибами\n\n⏱ Время: 30 мин | 👥 Порций: 2 | 🔥 Способ: Плита\n\n*Пищевая ценность (на 1 порцию):*\nКалорийность: 520 ккал | Б: 18 г | Ж: 22 г | У: 60 г\n\n"
     "*Продукты:*\n- Паста — 200 г\n- Грибы — 300 г\n- Сливки — 200 мл\n\n*Инструкция:*\n1. Отварите пасту 10 минут.\n2. Обжарьте грибы 7 минут.\n3. Влейте сливки, прогрейте и смешайте с пастой.")
D = {"cuisine": "classic", "time": "30 мин", "servings": 2, "portion": "normal", "appliance": ["stove"]}
bid = book.add(D, "Паста с грибами", R, ["паста"])
assert bid, "рецепт не принят книгой"

seen = []


async def fake_llm(messages, max_tokens=3000):
    seen.append(messages[-1]["content"])
    if fake_llm.fail:
        raise RuntimeError("deepseek: 500")
    return ("🎯 Под вас: без сливок, порция 450 ккал\n\n🍽 Паста с грибами\n\n*Продукты:*\n- Паста — 160 г\n- Грибы — 300 г\n\n"
            "*Инструкция:*\n1. Отварите пасту.\n\nПРОДУКТЫ_СПИСОК: паста, грибы")


fake_llm.fail = False
tailor.call_llm_with_fallback = fake_llm

FREE, PRO = 793001, 793002
accounts.grant_days(PRO, 30, "promo:T")
accounts.consent_set(PRO)
accounts.goals_set(PRO, feats=["never"], never="сливки", kcal=1800)
check("профиль: исключения и цель видны «Под меня»", "сливки" in tailor.summary(PRO) and "1800" in tailor.summary(PRO), tailor.summary(PRO))


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        d = method.model_dump(exclude_none=True)
        self.calls.append((name, d))
        if name in ("SendMessage", "EditMessageText", "SendPhoto"):
            return Message(message_id=9900, date=dt.now(), chat=Chat(id=d.get("chat_id") or 1, type="private"), text=d.get("text", "x")).as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


seq = [0]


def upd(uid, data=None, text=None):
    seq[0] += 1
    u, c = User(id=uid, is_bot=False, first_name="T"), Chat(id=uid, type="private")
    if data:
        m = Message(message_id=100 + seq[0], date=dt.now(), chat=c, text="x")
        return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=u, chat_instance="x", message=m, data=data))
    return Update(update_id=seq[0], message=Message(message_id=100 + seq[0], date=dt.now(), chat=c, from_user=u, text=text))


def texts(s):
    return [d.get("text", "") for n, d in s.calls if n in ("SendMessage", "EditMessageText")]


async def main():
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    await botmod.dp.feed_update(b, upd(PRO, f"bk:v:{bid}"))
    kb = str([d.get("reply_markup") for n, d in s.calls if n == "SendMessage"][-1])
    check("в книге под рецептом «🎯 Под меня»", f"tl:o:{bid}" in kb and "🎯 Под меня" in kb)
    s.calls.clear()
    await botmod.dp.feed_update(b, upd(FREE, f"bk:v:{bid}"))
    check("без подписки кнопка с 💎", "💎 Под меня" in str([d.get("reply_markup") for n, d in s.calls if n == "SendMessage"][-1]))
    s.calls.clear()
    await botmod.dp.feed_update(b, upd(FREE, f"tl:o:{bid}"))
    check("без подписки: объяснение и кнопка подписки, ИИ не спрошен", any("в подписке" in t for t in texts(s)) and not seen)

    s.calls.clear()
    await botmod.dp.feed_update(b, upd(PRO, f"tl:o:{bid}"))
    check("с подпиской: что учту из профиля и вопрос о пожеланиях", any("Учту из профиля" in t and "сливки" in t for t in texts(s)), str(texts(s))[:300])
    before = usage._count(f"SELECT COUNT(*) FROM gens WHERE uid='{PRO}' AND kind='missing'")
    await botmod.dp.feed_update(b, upd(PRO, text="на 4 порции"))
    out = texts(s)
    check("пожелания и профиль ушли в ИИ", seen and "ИСКЛЮЧИТЬ: сливки" in seen[-1] and "ЦЕЛЬ НА ДЕНЬ" in seen[-1] and "на 4 порции" in seen[-1], (seen or [""])[-1][:300])
    check("пришёл подстроенный рецепт с кнопками", any(t.startswith("🎯 Под вас") for t in out))
    check("попытка списана как замена", usage._count(f"SELECT COUNT(*) FROM gens WHERE uid='{PRO}' AND kind='missing'") == before + 1)

    fake_llm.fail = True
    s.calls.clear()
    await botmod.dp.feed_update(b, upd(PRO, f"tl:o:{bid}"))
    await botmod.dp.feed_update(b, upd(PRO, "tl:go"))
    check("сбой ИИ: понятный текст, попытка возвращена", any("попытка не списана" in t for t in texts(s))
          and usage._count(f"SELECT COUNT(*) FROM gens WHERE uid='{PRO}' AND kind='missing'") == before + 1)
    fake_llm.fail = False


asyncio.run(main())

cl = TestClient(web_api.app)


def dev_for(uid):
    d = cl.post("/api/device").json()["device"]
    accounts._q("INSERT INTO links(dev_id, tg_id, ts) VALUES(?,?,?)", (web_api.web_db.device_id(d), uid, time.time()))
    return {"X-Device": d}


Hf, Hp = dev_for(FREE), dev_for(PRO)
check("сайт: без подписки — 403", cl.post("/api/tailor", json={"book_id": bid, "wishes": "без лука"}, headers=Hf).status_code == 403)
check("сайт: замок в профиле", cl.get("/api/me", headers=Hf).json()["feat"].get("tailor") is False and cl.get("/api/me", headers=Hp).json()["feat"].get("tailor") is True)
info = cl.get("/api/tailor/info", headers=Hp).json()
check("сайт: что учту из профиля", info["on"] and "сливки" in info["known"])
j = cl.post("/api/tailor", json={"book_id": bid, "wishes": ""}, headers=Hp).json()
res = {}
for _ in range(50):
    res = cl.get(f"/api/job/{j['job']}", headers=Hp).json()
    if res["status"] != "running":
        break
    time.sleep(0.1)
check("сайт: подстроенный рецепт и список покупок", res.get("status") == "done" and res["result"]["text"].startswith("🎯 Под вас") and res["result"]["shopping"], str(res)[:300])
check("сайт: несуществующий рецепт — 404", cl.post("/api/tailor", json={"book_id": 999999}, headers=Hp).status_code == 404)
idx = open("web/index.html", encoding="utf-8").read()
check("сайт: кнопка в книге и замок", "tailorBox(r.id)" in idx and 'data-gate="tailor"' in idx and '"/api/tailor"' in idx)

print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
