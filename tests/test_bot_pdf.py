"""PDF из бота: рецепт, меню на день, план — файл приходит в чат (читать без интернета)."""
import asyncio
import json
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
import day_menu
import week_menu

CHAT = 777000006
fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.docs = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        if name == "SendDocument":
            f = method.document
            self.docs.append((f.filename, bytes(f.data), method.caption))
            self.calls.append((name, {"caption": method.caption}))
        else:
            self.calls.append((name, method.model_dump(exclude_none=True)))
        if name in ("SendMessage", "EditMessageText", "SendDocument"):
            return Message(message_id=900 + len(self.calls), date=datetime.now(), chat=Chat(id=CHAT, type="private"),
                           text=getattr(method, "text", "") or "").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


user = User(id=CHAT, is_bot=False, first_name="T")
chat = Chat(id=CHAT, type="private")
RECIPE = ("🍽 Каша с яйцом\n\nПродукты:\n- Овсяные хлопья — 40 г (≈ 4 ст. л.)\n- Яйцо — 1 шт\n\nИнструкция:\n1. Вари 5–6 мин.\n2. Подай.")
uid = [0]


def cb(data, text=RECIPE):
    uid[0] += 1
    msg = Message(message_id=600, date=datetime.now(), chat=chat, text=text, from_user=user)
    return Update(update_id=uid[0], callback_query=CallbackQuery(id=str(uid[0]), from_user=user, chat_instance="x", message=msg, data=data))


async def main():
    s = FakeSession()
    b = Bot(token="123456:TEST-TOKEN-NOT-REAL-1234567890abcdef", session=s)
    await botmod.dp.feed_update(b, cb("pdf:r"))
    check("рецепт: PDF отправлен файлом", len(s.docs) == 1 and s.docs[0][0].endswith(".pdf") and s.docs[0][1][:4] == b"%PDF", str(s.calls)[:200])
    check("имя файла — название блюда", s.docs and "Каша" in s.docs[0][0], str(s.docs[0][0]) if s.docs else "")
    check("подпись про чтение без интернета", s.docs and "без интернета" in s.docs[0][2])

    day_menu._save_menu(CHAT, {"order": ["lunch"], "meals": {"lunch": {"title": "Суп", "text": "🍽 Суп\n\nИнструкция:\n1. Вари 20 мин.", "ok": True}},
                               "shopping_terms": ["фарш", "лук"], "base_data": {}})
    await botmod.dp.feed_update(b, cb("pdf:d", text="Меню"))
    check("меню на день: PDF", len(s.docs) == 2 and s.docs[1][1][:4] == b"%PDF")

    week_menu._save(CHAT, {"base_data": {}, "meal_keys": ["lunch"], "days_total": 3, "inventory": {"рис": 1}, "inventory_initial": {"рис": 600},
                           "basket_text": "Корзина: рис", "history_titles": [], "current_day": 2, "progress_index": 0,
                           "days": {"1": {"order": ["lunch"], "meals": {"lunch": {"title": "Плов", "text": "🍽 Плов\n\nИнструкция:\n1. Вари 25 мин."}},
                                          "shopping_message": "", "shopping_terms": []}}})
    await botmod.dp.feed_update(b, cb("pdf:w", text="План"))
    check("план: PDF", len(s.docs) == 3 and s.docs[2][1][:4] == b"%PDF")

    s.calls.clear()
    await botmod.dp.feed_update(b, cb("pdf:x", text="что-то"))
    ans = [p for n, p in s.calls if n == "AnswerCallbackQuery"]
    check("неизвестный вид — подсказка, а не падение", ans and ans[0].get("show_alert") is True and len(s.docs) == 3)

    import keyboards as K
    check("кнопка PDF под рецептом", any("PDF" in x.text for r in K.recipe_result_kb().inline_keyboard for x in r))
    check("кнопка PDF в меню на день", any("PDF" in x.text for r in day_menu.build_summary_view(CHAT)[1].inline_keyboard for x in r))
    check("кнопка PDF в плане", any("PDF" in x.text for r in week_menu.build_status_view(CHAT)[1].inline_keyboard for x in r))


asyncio.run(main())
for fname in ("day_menu.json", "week_menu.json"):
    pth = os.path.join(PROJ, fname)
    if os.path.exists(pth):
        data = json.load(open(pth, encoding="utf-8"))
        data.pop(str(CHAT), None)
        json.dump(data, open(pth, "w", encoding="utf-8"), ensure_ascii=False)
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
sys.exit(1 if fails else 0)
