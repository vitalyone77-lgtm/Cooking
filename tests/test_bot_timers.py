"""Таймеры шагов в боте: кнопки под рецептом и сигналы (время подменено)."""
import asyncio
import os
import sys
from datetime import datetime

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Update, CallbackQuery, Message, Chat, User

import bot as botmod
import timers

CHAT = 777000005
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append((type(method).__name__, method.model_dump(exclude_none=True)))
        if type(method).__name__ in ("SendMessage", "EditMessageText"):
            return Message(message_id=900 + len(self.calls), date=datetime.now(), chat=Chat(id=CHAT, type="private"),
                           text=getattr(method, "text", "") or "").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


RECIPE = ("🍽 Каша с яйцом\n\nПродукты:\n- Овсяные хлопья — 60 г (≈ 6 ст. л.)\n- Яйцо — 1 шт\n\nИнструкция:\n"
          "1. Доведи молоко до кипения.\n2. Всыпь овсянку и вари 5–6 мин, помешивая.\n3. Отвари яйцо 8 мин.\n4. Подай.")

# разбор времени
check("шаги со временем: 5–6 и 8", timers.step_timers(RECIPE) == [(2, 5, 6), (3, 8, 8)], str(timers.step_timers(RECIPE)))
check("часы переводятся в минуты", timers.step_timers("Инструкция:\n1. Туши 1,5 часа.") == [(1, 90, 90)])
check("секунды и «40 мин после финиша» в советах игнорируются", timers.step_timers("Инструкция:\n1. Жарь 30 секунд.\nСовет: ешь в течение 40 мин.") == [])
check("подписи", timers.label(5, 6) == "5–6 мин" and timers.label(8, 8) == "8 мин" and timers.fmt_min(90) == "1 ч 30 мин")

user = User(id=CHAT, is_bot=False, first_name="T")
chat = Chat(id=CHAT, type="private")
uid = [0]


def cb(data):
    uid[0] += 1
    msg = Message(message_id=600, date=datetime.now(), chat=chat, text=RECIPE, from_user=user)
    return Update(update_id=uid[0], callback_query=CallbackQuery(id=str(uid[0]), from_user=user, chat_instance="x", message=msg, data=data))


async def main():
    s = FakeSession()
    b = Bot(token="123456:TEST-TOKEN-NOT-REAL-1234567890abcdef", session=s)

    # кнопки под рецептом
    kbm = botmod.recipe_kb(RECIPE)
    txt = [x.text for r in kbm.inline_keyboard for x in r]
    check("под рецептом — режим готовки (таймеры теперь внутри шагов) и список покупок", "👨‍🍳 Готовить по шагам" in txt and "🛒 Продукты и магазины" in txt and not any(t.startswith("⏱") for t in txt), str(txt))
    check("у рецепта без времени кнопок таймеров нет", not any(t.startswith("⏱") for r in botmod.recipe_kb("🍽 X\n\nИнструкция:\n1. Смешай.").inline_keyboard for t in [x.text for x in r]))

    # нажатие: время подменяем, фиксируем паузы
    sleeps = []

    async def fake_sleep(sec):
        sleeps.append(sec)
    botmod._timer_sleep = fake_sleep
    await botmod.dp.feed_update(b, cb("tmr:5:6:2"))
    await asyncio.gather(*list(botmod._active_timers.get(CHAT, set())))
    sends = [p["text"] for n, p in s.calls if n == "SendMessage"]
    ans = [p for n, p in s.calls if n == "AnswerCallbackQuery"]
    check("подтверждение запуска", ans and "Таймер запущен" in ans[0].get("text", "") and "5 мин" in ans[0]["text"], str(ans))
    check("паузы 5 мин и ещё 1 мин", sleeps == [300, 60], str(sleeps))
    check("два сигнала: «можно снимать» и «время вышло»", len(sends) == 2 and "можно снимать" in sends[0] and "время вышло" in sends[1], str(sends))

    s.calls.clear(); sleeps.clear()
    await botmod.dp.feed_update(b, cb("tmr:8:8:3"))
    await asyncio.gather(*list(botmod._active_timers.get(CHAT, set())))
    sends = [p["text"] for n, p in s.calls if n == "SendMessage"]
    check("одно время: один сигнал в конце", sleeps == [480] and len(sends) == 1 and "время вышло (8 мин)" in sends[0], f"{sleeps} {sends}")
    check("таймеры убираются после сигнала", not botmod._active_timers.get(CHAT))

    s.calls.clear()
    await botmod.dp.feed_update(b, cb("tmr:abc"))
    ans = [p for n, p in s.calls if n == "AnswerCallbackQuery"]
    check("битые данные не роняют бота", ans and ans[0].get("show_alert") is True)

asyncio.run(main())
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
sys.exit(1 if fails else 0)
