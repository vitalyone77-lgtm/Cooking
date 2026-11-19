"""«Что приготовить из полки»: рецепты книги по продуктам с полки без ИИ (соль и специи не считаются), бот и сайт,
«Подобрать новый из полки» подставляет продукты в подбор."""
import asyncio
import os
import sys
import time
from datetime import datetime as dt

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_sc.db", "accounts_sc.db", "accounts_sc.db-wal", "accounts_sc.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_sc.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_sc.db"
os.environ["WEB_DATA_DIR"] = "web_data_sc"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from fastapi.testclient import TestClient

import tg_safety
tg_safety.FloodGuard.__init__.__defaults__ = (100000, 10)
import accounts
import book
import bot as botmod
import bot_profile
import bot_vision
import web_api

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


def rec(title, products):
    return (f"🍽 {title}\n\n⏱ Время: 30 мин | 👥 Порций: 2 | 🔥 Способ: Плита\n\n*Пищевая ценность (на 1 порцию):*\nКалорийность: 400 ккал | Б: 20 г | Ж: 10 г | У: 50 г\n\n"
            "*Продукты:*\n" + "\n".join(f"- {p} — 100 г" for p in products) + "\n- Соль — по вкусу\n\n*Инструкция:*\n1. Подготовьте продукты.\n2. Варите 20 минут.\n3. Подавайте горячим.")


D = {"cuisine": "classic", "time": "30 мин", "servings": 2, "portion": "normal", "appliance": ["stove"]}
b1 = book.add(D, "Гречка с грибами", rec("Гречка с грибами", ["Гречка", "Грибы", "Масло подсолнечное"]), [])
b2 = book.add(D, "Рис с овощами", rec("Рис с овощами", ["Рис", "Морковь", "Лук"]), [])
b3 = book.add(D, "Сырники", rec("Сырники", ["Творог", "Мука пшеничная", "Яйцо"]), [])
assert b1 and b2 and b3
found = book.from_shelf(["гречка", "масло подсолнечное", "соль", "мука пшеничная"])
check("лучшее сверху: гречка (2 из 3), потом сырники (1 из 3); рис без совпадений не показан",
      [r["title"] for r in found] == ["Гречка с грибами", "Сырники"] and found[0]["total"] == 3 and len(found[0]["have"]) == 2, str([(r["title"], r["have"]) for r in found]))
check("соль на полке не делает рецепт «подходящим»", book.from_shelf(["соль", "перец"]) == [])

U = 791001
accounts.grant_days(U, 30, "promo:T")
accounts.consent_set(U)
accounts.goals_set(U, feats=["shelf"])
for it in ("гречка", "масло подсолнечное"):
    accounts.shelf_add(U, it)


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
        if name in ("SendMessage", "EditMessageText"):
            return Message(message_id=9900, date=dt.now(), chat=Chat(id=U, type="private"), text=d.get("text", "x")).as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


seq = [0]


def cb(data):
    seq[0] += 1
    m = Message(message_id=100 + seq[0], date=dt.now(), chat=Chat(id=U, type="private"), text="x")
    return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=User(id=U, is_bot=False, first_name="T"), chat_instance="x", message=m, data=data))


started = []


async def fake_start(message, state, text):
    started.append(text)


async def main():
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    text, kb = bot_profile.shelf_view(U)
    check("в полке кнопка «Что приготовить из полки»", any(x.callback_data == "pf:shck" for r in kb.inline_keyboard for x in r))
    await botmod.dp.feed_update(b, cb("pf:shck"))
    ed = [d for n, d in s.calls if n == "EditMessageText"]
    kbs = str(ed[-1].get("reply_markup")) if ed else ""
    check("бот: рецепты из книги с «есть N из M»", f"bk:v:{b1}" in kbs and "есть 2 из 3" in kbs, kbs[:300])
    orig = bot_vision.hooks.get("start_recipe")
    bot_vision.hooks["start_recipe"] = fake_start
    await botmod.dp.feed_update(b, cb("pf:shgen"))
    bot_vision.hooks["start_recipe"] = orig
    check("«Подобрать новый из полки»: продукты с полки в подбор", started and "гречка" in started[-1], str(started))


asyncio.run(main())

cl = TestClient(web_api.app)
dev = cl.post("/api/device").json()["device"]
accounts._q("INSERT INTO links(dev_id, tg_id, ts) VALUES(?,?,?)", (web_api.web_db.device_id(dev), U, time.time()))
r = cl.get("/api/shelf/cook", headers={"X-Device": dev})
check("сайт: те же рецепты, с тем, что есть дома", r.status_code == 200 and r.json()["items"][0]["title"] == "Гречка с грибами"
      and r.json()["items"][0]["total"] == 3, r.text[:200])
idx = open("web/index.html", encoding="utf-8").read()
check("сайт: кнопка в полке и экран", 'data-tab-go="shelfcook"' in idx and "shelfcook:viewShelfCook" in idx)

print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
