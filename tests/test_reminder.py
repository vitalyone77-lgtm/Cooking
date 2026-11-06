"""Сценарий напоминания: короткое превью → «Готовить» (полный рецепт) → «Выключить» → /remind."""
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

import storage
storage.STORAGE_FILE = storage.Path("/tmp/ct/_user_data_test.json")
import bot as botmod
import reminders

CHAT = 777000002
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


RECIPE = ("🍽 Куриное филе с киноа и овощами на сковороде\n\n⏱ Время: 30 мин | 👥 Порций: 1 | 🔥 Способ: плита\n\n"
          "Пищевая ценность (на 1 порцию):\nКалорийность: 545 ккал\n\nПродукты:\n- Куриное филе — 150 г\n- Киноа — 60 г\n\n"
          "Инструкция:\n1. Сварить киноа.\nПРОДУКТЫ_СПИСОК: куриное филе, киноа")


async def fake_search(data):
    return "q", []


async def fake_generate(data, q, r):
    fake_generate.seen = data
    return RECIPE

reminders.search_recipes = fake_search
reminders.generate_recipe = fake_generate

user = User(id=CHAT, is_bot=False, first_name="T")
chat = Chat(id=CHAT, type="private")
n = [0]


def upd_cb(data):
    n[0] += 1
    msg = Message(message_id=500, date=datetime.now(), chat=chat, text="превью", from_user=user)
    return Update(update_id=n[0], callback_query=CallbackQuery(id=str(n[0]), from_user=user, chat_instance="x",
                                                                message=msg, data=data))


def upd_msg(text):
    n[0] += 1
    return Update(update_id=n[0], message=Message(message_id=600 + n[0], date=datetime.now(), chat=chat, text=text,
                                                  from_user=user))


def buttons(p):
    return [b["text"] for row in (p.get("reply_markup") or {}).get("inline_keyboard", []) for b in row]


reminders.usage.reminder_due = lambda cid, gaps: 1   # кому слать (4+6 дней тишины) проверяет test_v2_bot


async def main():
    s = FakeSession()
    b = Bot(token="123456:TEST-TOKEN-NOT-REAL-1234567890abcdef", session=s)
    storage.save_last_request(CHAT, {"cuisine": "balanced", "time": "30", "servings": 1, "preferred": "курица"}, "Старое блюдо")

    await reminders.send_daily_reminders(b, botmod.dp)
    sends = [p for nm, p in s.calls if nm == "SendMessage" and p["chat_id"] == CHAT]
    check("рассылка: одно сообщение", len(sends) == 1, str(s.calls))
    t = sends[0]["text"] if sends else ""
    check("превью: приветствие, название и время", t.startswith("👋 Давно не виделись") and "🍽 Куриное филе" in t
          and "⏱ Время: 30 мин" in t, t)
    check("превью без продуктов и инструкции", "Продукты" not in t and "Инструкция" not in t and "Пищевая" not in t, t)
    check("кнопки: Готовить / Найти другое / Выключить", sends and buttons(sends[0]) ==
          ["🍳 Готовить", "🔍 Найти другое", "🔕 Выключить напоминания"], str(buttons(sends[0]) if sends else ""))
    check("прошлое пожелание не передаётся в ИИ", fake_generate.seen.get("preferred") == "" and "dish_title" not in fake_generate.seen)
    check("прошлое блюдо — в списке «не повторять»", "Старое блюдо" in fake_generate.seen.get("avoid_titles", []))
    hint1 = fake_generate.seen.get("variety_hint")
    check("есть тип блюда для разнообразия", bool(hint1))
    await reminders.send_daily_reminders(b, botmod.dp)
    seen2 = fake_generate.seen
    check("на следующий день вчерашнее блюдо в «не повторять»", "Куриное филе с киноа и овощами на сковороде" in seen2.get("avoid_titles", []),
          str(seen2.get("avoid_titles")))
    check("на следующий день другой тип блюда", seen2.get("variety_hint") != hint1, f"{hint1} / {seen2.get('variety_hint')}")
    check("служебные поля не уходят в ИИ", not ({"reminder_history", "reminder_recipe", "reminders_off"} & set(seen2)))
    s.calls.clear()

    s.calls.clear()
    await botmod.dp.feed_update(b, upd_cb("reminder:cook"))
    sends = [p for nm, p in s.calls if nm == "SendMessage"]
    check("«Готовить» присылает полный рецепт", sends and "Инструкция" in sends[0]["text"] and "ПРОДУКТЫ_СПИСОК" not in sends[0]["text"],
          str(s.calls)[:300])
    check("под рецептом — «Найти продукты»/«В избранное»", sends and any("Продукты и магазин" in x for x in buttons(sends[0])))
    check("рецепт переживает перезапуск (сохранён в файле)", storage.get_reminder_recipe(CHAT) is not None)

    s.calls.clear()
    await botmod.dp.feed_update(b, upd_cb("reminder:next"))
    check("«Найти другое» открывает анкету", any(nm == "SendMessage" and "кухни" in p.get("text", "").lower() or
                                                  nm == "SendMessage" and "параметр" in p.get("text", "") for nm, p in s.calls), str(s.calls)[:300])

    s.calls.clear()
    await botmod.dp.feed_update(b, upd_cb("reminder:off"))
    check("«Выключить» выключает", not storage.reminders_enabled(CHAT))
    s.calls.clear()
    await reminders.send_daily_reminders(b, botmod.dp)
    check("выключенному рассылка не идёт", not [1 for nm, p in s.calls if nm == "SendMessage" and p["chat_id"] == CHAT])

    storage.save_last_request(CHAT, {"cuisine": "classic"}, "Другое")
    check("новый запрос не включает рассылку обратно", not storage.reminders_enabled(CHAT))

    s.calls.clear()
    await botmod.dp.feed_update(b, upd_msg("/remind"))
    check("/remind включает", storage.reminders_enabled(CHAT) and any("включены" in p.get("text", "") for nm, p in s.calls))
    await botmod.dp.feed_update(b, upd_msg("/remind"))
    check("/remind снова выключает", not storage.reminders_enabled(CHAT))

asyncio.run(main())
os.remove("/tmp/ct/_user_data_test.json")
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
sys.exit(1 if fails else 0)
