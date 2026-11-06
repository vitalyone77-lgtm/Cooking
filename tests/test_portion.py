"""Размер порции: промпты, сохранение, переключатель в боте, веб-параметры."""
import asyncio
import os
import sys
from datetime import datetime

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
os.environ["WEB_DATA_DIR"] = "/tmp/ct/web_data_test4"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.context import FSMContext
from aiogram.types import Update, CallbackQuery, Message, Chat, User

import storage
storage.STORAGE_FILE = storage.Path("/tmp/ct/_ud_portion.json")
import bot as botmod
import prompts
import cuisines

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# промпты
base = {"cuisine": "classic", "time": "30 мин", "servings": 1, "appliance_labels": ["Плита"]}
up_small = prompts.build_user_prompt({**base, "portion": "small"}, "q", "r")
up_norm = prompts.build_user_prompt({**base, "portion": "normal"}, "q", "r")
up_none = prompts.build_user_prompt(base, "q", "r")
check("маленькая порция: инструкция в промпте рецепта", "МАЛЕНЬКАЯ порция" in up_small and "60%" in up_small)
check("обычная и без размера: инструкции нет", "РАЗМЕР ПОРЦИИ" not in up_norm and "РАЗМЕР ПОРЦИИ" not in up_none)
check("большая порция", "БОЛЬШАЯ порция" in prompts.build_user_prompt({**base, "portion": "large"}, "q", "r"))
dp = prompts.build_day_menu_user_prompt([{"label": "Обед", "search_query": "q", "search_results_text": "r", "macro_goal": None, "rule": ""}], {**base, "portion": "small"})
check("меню на день учитывает размер", "МАЛЕНЬКАЯ порция" in dp)
wp = prompts.build_week_basket_user_prompt(["Обед"], 3, {**base, "portion": "small"})
check("корзина плана учитывает размер", "МАЛЕНЬКАЯ порция" in wp)
check("цель по КБЖУ важнее размера", "цель по КБЖУ" in cuisines.portion_instruction({"portion": "small"}))

# хранение
CHAT = 777000004
check("по умолчанию — обычная", storage.get_portion(CHAT) == "normal")
storage.set_portion(CHAT, "small")
check("размер запоминается", storage.get_portion(CHAT) == "small")
storage.save_last_request(CHAT, {"cuisine": "classic", "time": "30 мин"}, "Блюдо")
check("новый запрос не сбрасывает размер", storage.get_portion(CHAT) == "small")
storage.save_last_request(CHAT, {"cuisine": "classic", "portion": "large"}, "Блюдо")
check("размер из анкеты сохраняется", storage.get_portion(CHAT) == "large")


# бот: переключатель на экране проверки
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


user = User(id=CHAT, is_bot=False, first_name="T")
chat = Chat(id=CHAT, type="private")
SUMMARY = ("📋 Проверим запрос:\n\nТип питания: Классическая\nВремя: 30 мин\nПорций: 1\nРазмер порции: Побольше\nТехника: Плита\n")


async def main():
    s = FakeSession()
    b = Bot(token="123456:TEST-TOKEN-NOT-REAL-1234567890abcdef", session=s)
    storage.set_portion(CHAT, "normal")

    def upd(i):
        msg = Message(message_id=500, date=datetime.now(), chat=chat, text=SUMMARY, from_user=user)
        return Update(update_id=i, callback_query=CallbackQuery(id=str(i), from_user=user, chat_instance="x", message=msg, data="portion:cycle"))

    await botmod.dp.feed_update(b, upd(1))
    edit = next(p for n, p in s.calls if n == "EditMessageText")
    btns = [x["text"] for row in edit["reply_markup"]["inline_keyboard"] for x in row]
    check("обычная → побольше: текст сводки обновлён", "Размер порции: Побольше" in edit["text"] and "Время: 30 мин" in edit["text"], edit["text"])
    check("кнопка показывает новый размер", any("Побольше" in t for t in btns), str(btns))
    check("выбор сохранён", storage.get_portion(CHAT) == "large")
    s.calls.clear()
    await botmod.dp.feed_update(b, upd(2))
    check("побольше → поменьше", storage.get_portion(CHAT) == "small")
    await botmod.dp.feed_update(b, upd(3))
    check("поменьше → обычная (по кругу)", storage.get_portion(CHAT) == "normal")
    key = StorageKey(bot_id=b.id, chat_id=CHAT, user_id=CHAT)
    data = await FSMContext(storage=botmod.dp.storage, key=key).get_data()
    check("размер лежит и в данных анкеты", data.get("portion") == "normal", str(data))

    # анкета → генерация получает размер
    got = {}

    async def fake_search(d):
        return "q", []

    async def fake_gen(d, q, r):
        got["d"] = d
        return "🍽 Блюдо\n\nПродукты:\n- рис — 100 г\nПРОДУКТЫ_СПИСОК: рис"
    botmod.search_recipes, botmod.generate_recipe = fake_search, fake_gen
    storage.set_portion(CHAT, "small")
    fsm = FSMContext(storage=botmod.dp.storage, key=key)
    await fsm.set_data({"cuisine": "classic", "time": "30 мин", "servings": 1, "appliance": ["stove"], "appliance_labels": ["Плита"]})
    from states import RecipeForm
    await fsm.set_state(RecipeForm.confirm)
    msg = Message(message_id=501, date=datetime.now(), chat=chat, text=SUMMARY, from_user=user)
    await botmod.dp.feed_update(b, Update(update_id=10, callback_query=CallbackQuery(
        id="10", from_user=user, chat_instance="x", message=msg, data="confirm:go")))
    check("в генерацию ушёл размер из сохранённых настроек", got.get("d", {}).get("portion") == "small", str(got))

asyncio.run(main())

# веб
import web_api
from fastapi.testclient import TestClient
c = TestClient(web_api.app)
meta = c.get("/api/meta").json()
check("веб: размеры порции в meta", [p["key"] for p in meta["portions"]] == ["small", "normal", "large"], str(meta.get("portions")))
p = web_api.Params(appliance=["stove"], portion="small")
check("веб: параметр попадает в данные", p.to_data()["portion"] == "small")
try:
    web_api.Params(appliance=["stove"], portion="huge")
    ok = False
except Exception:
    ok = True
check("веб: неверный размер отклоняется", ok)
check("веб: по умолчанию обычная", web_api.Params(appliance=["stove"]).to_data()["portion"] == "normal")
check("на сайте есть выбор размера порции", "Размер порции" in open("/tmp/ct/web/index.html", encoding="utf-8").read())

os.remove("/tmp/ct/_ud_portion.json")
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
sys.exit(1 if fails else 0)
