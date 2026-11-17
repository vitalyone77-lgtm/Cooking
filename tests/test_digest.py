"""Итог недели: кому приходит, что в нём, «Не присылать» и включение обратно в Настройках, без повторной отправки за неделю."""
import asyncio
import os
import sys
import time
from datetime import datetime as dt

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_dg.db", "accounts_dg.db", "accounts_dg.db-wal", "accounts_dg.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_dg.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_dg.db"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User

import tg_safety
tg_safety.FloodGuard.__init__.__defaults__ = (100000, 10)
import accounts
import bot as botmod
import digest
import usage

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


A, B, Q, VK = 795001, 795002, 795003, accounts.VK_OFFSET + 5
for k in ("recipe", "recipe", "recipe", "daymenu"):
    usage.log("bot", A, k)
usage.event("cook_start", A)
accounts.goals_set(A, kcal=2000)
today = time.strftime("%Y-%m-%d")
accounts.diary_add(A, "Каша", 2500, 20, 10, 300)
accounts.weight_set(A, 80.4, time.strftime("%Y-%m-%d", time.localtime(time.time() - 5 * 86400)))
accounts.weight_set(A, 79.8, today)
usage.log("web", B, "recipe")
usage.log("reminder", Q, "reminder")         # только напоминание — не активность
usage.log("web", VK, "recipe")               # ВК — Telegram-итог не отправить

c = digest.candidates(time.time() - digest.WEEK)
check("кому: активные в Telegram, без «только напоминание» и без ВК", A in c and B in c and Q not in c and VK not in c, str(c))
t = digest.build(A)
check("итог: рецепты и меню", "3 рецепта" in t and "1 меню на день" in t, t)
check("итог: готовили по шагам", "Готовили по шагам с таймерами: 1 раз" in t, t)
check("итог: дневник и цель", "1 из 7 дней" in t and "2500 ккал" in t and "цель 2000, +500" in t, t)
check("итог: вес", "80.4 → 79.8 кг (−0.6)" in t, t)
check("совет по данным: больше цели — «По КБЖУ»", "По КБЖУ" in t, t)
check("неактивному итог не собирается", digest.build(Q) is None)
check("совет новичку дневника", "дневник питания" in digest.build(B))

C = A
user, chat = User(id=C, is_bot=False, first_name="T"), Chat(id=C, type="private")


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
            return Message(message_id=9900, date=dt.now(), chat=Chat(id=d.get("chat_id") or C, type="private"), text="x").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


async def main():
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    n = await digest.job(b)
    to = sorted(d["chat_id"] for nm, d in s.calls if nm == "SendMessage")
    check("рассылка: двоим активным", n == 2 and to == sorted([A, B]), str(to))
    check("под итогом «Не присылать итоги»", any("dg:off" in str(d.get("reply_markup")) for nm, d in s.calls if nm == "SendMessage"))
    s.calls.clear()
    check("второй раз за неделю не присылает", await digest.job(b) == 0)
    m = Message(message_id=5, date=dt.now(), chat=chat, text="итог")
    await botmod.dp.feed_update(b, Update(update_id=1, callback_query=CallbackQuery(id="1", from_user=user, chat_instance="x", message=m, data="dg:off")))
    check("«Не присылать»: выключено, объяснено, где включить", not digest.enabled(A) and any("Настройки" in d.get("text", "") for nm, d in s.calls if nm == "AnswerCallbackQuery"))
    for k in list(accounts.sess_items("digest:")):
        accounts._q("DELETE FROM sess WHERE key=?", (k[0],))
    s.calls.clear()
    await digest.job(b)
    check("выключившему не приходит", A not in [d["chat_id"] for nm, d in s.calls if nm == "SendMessage"])
    accounts.consent_set(A)
    await botmod.dp.feed_update(b, Update(update_id=2, callback_query=CallbackQuery(id="2", from_user=user, chat_instance="x", message=m, data="pf:set")))
    kb = str([d.get("reply_markup") for nm, d in s.calls if nm in ("EditMessageText", "SendMessage")][-1])
    check("в Настройках есть переключатель итога", "pf:dg" in kb, kb[:300])
    await botmod.dp.feed_update(b, Update(update_id=3, callback_query=CallbackQuery(id="3", from_user=user, chat_instance="x", message=m, data="pf:dg")))
    check("включили обратно в Настройках", digest.enabled(A))


asyncio.run(main())
print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
