"""Общий список покупок, который копится по ходу: «🛒 В общий список» под рецептом в боте и на сайте, одна корзина на оба,
отмеченное не сбрасывается при открытии, после сборки — с чистого; подсказка после первого рецепта на сайте."""
import asyncio
import os
import sys
import time
from datetime import datetime as dt

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_cm.db", "accounts_cm.db", "accounts_cm.db-wal", "accounts_cm.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_cm.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_cm.db"
os.environ["WEB_DATA_DIR"] = "web_data_cm"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from fastapi.testclient import TestClient

import tg_safety
tg_safety.FloodGuard.__init__.__defaults__ = (100000, 10)
import accounts
import bot as botmod
import favorites
import shoplists
import web_api

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


C = 794001
user, chat = User(id=C, is_bot=False, first_name="T"), Chat(id=C, type="private")
R1 = ("🍽 Куриный суп\n\n*Продукты:*\n- Курица — 300 г\n- Рис — 100 г\n- Морковь — 1 шт\n\n*Инструкция:*\n1. Сварите курицу 30 минут.\n2. Добавьте рис.")
R2 = ("🍽 Плов\n\n*Продукты:*\n- Курица — 400 г\n- Рис — 200 г\n- Лук — 1 шт\n\n*Инструкция:*\n1. Обжарьте курицу.\n2. Добавьте рис и воду.")


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
            return Message(message_id=9900, date=dt.now(), chat=chat, text=d.get("text", "x")).as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


seq = [0]


def cb(data, text, markup=None):
    seq[0] += 1
    m = Message(message_id=100 + seq[0], date=dt.now(), chat=chat, text=text, reply_markup=markup)
    return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=user, chat_instance="x", message=m, data=data))


async def main():
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    kb = botmod.recipe_kb(R1, C)
    check("под рецептом есть «🛒 В общий список покупок»", any(x.callback_data == "cb:add" for row in kb.inline_keyboard for x in row))
    await botmod.dp.feed_update(b, cb("cb:add", R1.replace("*", ""), kb))
    ans = [d for n, d in s.calls if n == "AnswerCallbackQuery"][-1]
    check("бот: рецепт в списке, сказано сколько", "Куриный суп" in ans.get("text", "") and "1" in ans.get("text", ""), str(ans))
    ed = [d for n, d in s.calls if n == "EditMessageReplyMarkup"]
    check("кнопка сменилась на «✅ В списке (1) — собрать»", ed and "В списке (1)" in str(ed[-1]) and "cb:o" in str(ed[-1]))
    check("рецепт сохранён в «Любимые»", any(f["title"] == "Куриный суп" for f in favorites.get_favorites(C)))
    await botmod.dp.feed_update(b, cb("cb:add", R2.replace("*", ""), botmod.recipe_kb(R2, C)))
    await botmod.dp.feed_update(b, cb("cb:add", R2.replace("*", ""), botmod.recipe_kb(R2, C)))
    check("повторное нажатие не дублирует", len(shoplists.combo_get(C)["ids"]) == 2 and len(favorites.get_favorites(C)) == 2)
    s.calls.clear()
    await botmod.dp.feed_update(b, cb("cb:o", "🛒 Общий список покупок"))
    v = [d for n, d in s.calls if n in ("EditMessageText", "SendMessage")][-1]
    check("открыли список — отмеченное на месте, «Собрать (2)» и «Очистить»", str(v).count("✅") >= 2 and "cb:go" in str(v) and "cb:clr" in str(v), str(v)[:400])
    return b, s


b, s = asyncio.run(main())

# сайт видит ту же корзину
cl = TestClient(web_api.app)
dev = cl.post("/api/device").json()["device"]
H = {"X-Device": dev}
accounts._q("INSERT INTO links(dev_id, tg_id, ts) VALUES(?,?,?)", (web_api.web_db.device_id(dev), C, time.time()))
ids = cl.get("/api/combo", headers=H).json()["ids"]
check("сайт: та же корзина, что в боте", len(ids) == 2, str(ids))
R3 = R1.replace("Куриный суп", "Суп с фрикадельками")
r = cl.post("/api/combo", json={"title": "Суп с фрикадельками", "text": R3}, headers=H)
check("сайт: «В общий список» добавляет", r.status_code == 200 and r.json()["n"] == 3, r.text)
check("бот видит добавленное на сайте", len(shoplists.combo_get(C)["ids"]) == 3)
check("сайт: короткий текст — отказ", cl.post("/api/combo", json={"text": "x"}, headers=H).status_code == 400)


async def go():
    s.calls.clear()
    await botmod.dp.feed_update(b, cb("cb:go", "🛒 Общий список покупок"))
    t = [d.get("text", "") for n, d in s.calls if n == "SendMessage"]
    check("собрали: курица сложилась (300+400+300 г)", t and "1 кг" in t[-1] or "1000 г" in (t[-1] if t else ""), (t[-1] if t else "")[:300])
    check("после сборки список пуст — следующий с чистого", shoplists.combo_get(C)["ids"] == [])


asyncio.run(go())
fid = favorites.get_favorites(C)[0]["id"]
cl.post("/api/combo", json={"ids": [fid]}, headers=H)
favorites.remove_favorite(C, fid)
check("удалённый из «Любимых» рецепт сам выпадает из списка", shoplists.combo_get(C)["ids"] == [])

idx = open("web/index.html", encoding="utf-8").read()
check("сайт: кнопка под рецептом и галочки из общей корзины", "data-combo-add" in idx and '"/api/combo"' in idx and 'on.indexOf(f.id)>=0?" checked"' in idx)
check("сайт: после первого рецепта — сохранить и войти через Telegram (один раз)", "firstRecipeCard(res)" in idx and 'ls("firstrec")' in idx)

print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
