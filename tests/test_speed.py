"""Кнопки отвечают сразу: пока в чате идёт долгая генерация (следующий день плана, вопрос к ИИ), остальные кнопки
не ждут её, на нажатие тяжёлой кнопки Telegram получает ответ мгновенно, и человек видит «готовлю»."""
import asyncio
import os
import sys
import time
from datetime import datetime as dt

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_sp.db", "accounts_sp.db", "accounts_sp.db-wal", "accounts_sp.db-shm"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_sp.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_sp.db"
os.environ["WEB_DATA_DIR"] = "web_data_sp"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User

import tg_safety
tg_safety.FloodGuard.__init__.__defaults__ = (100000, 10)
import accounts
import bot as botmod
import week_menu

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


C = 796001
user, chat = User(id=C, is_bot=False, first_name="T"), Chat(id=C, type="private")
SLOW = 2.0
T0 = [0.0]


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        d = method.model_dump(exclude_none=True)
        self.calls.append((time.monotonic() - T0[0], name, d))
        if name in ("SendMessage", "EditMessageText"):
            return Message(message_id=9900, date=dt.now(), chat=chat, text=d.get("text", "x")).as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


async def no_search(meal_keys, base):
    return [{"meal_key": k, "label": k, "search": ""} for k in meal_keys]


async def slow_llm(messages, max_tokens=0):
    await asyncio.sleep(SLOW)
    return ("🍽 Омлет\n\n*Продукты:*\n- Яйца — 2 шт\n\n*Инструкция:*\n1. Жарьте.\n" + week_menu.DAY_MENU_BLOCK_DELIMITER
            + "\nИСПОЛЬЗОВАНО_БАЗОВЫЕ: яйца=2")


week_menu.day_menu.prepare_meals_info = no_search
week_menu.build_day_menu_user_prompt = lambda info, base: "меню"
week_menu.call_llm_with_fallback = slow_llm

week_menu._save(C, {"base_data": {}, "meal_keys": ["breakfast"], "days_total": 3, "inventory": {"яйца": 10},
                    "inventory_initial": {"яйца": 10}, "basket_text": "корзина", "history_titles": ["Каша"],
                    "days": {"1": {"order": ["breakfast"], "meals": {"breakfast": {"title": "Каша", "text": "🍽 Каша\n\nКаша"}}}},
                    "current_day": 2, "progress_index": 0})
uid = [100]


def cb(data):
    uid[0] += 1
    m = Message(message_id=500, date=dt.now(), chat=chat, text="план")
    return Update(update_id=uid[0], callback_query=CallbackQuery(id=str(uid[0]), from_user=user, chat_instance="x", message=m, data=data))


def answered(s, cid, before):
    return any(n == "AnswerCallbackQuery" and d.get("callback_query_id") == cid and t < before for t, n, d in s.calls)


async def main():
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    T0[0] = time.monotonic()
    u_done = cb("weekmenu:done:0")           # последнее блюдо дня 1 → собирается день 2 (медленно)
    task = asyncio.create_task(botmod.dp.feed_update(b, u_done))
    await asyncio.sleep(0.3)
    u_light = cb("weekmenu:status")          # лёгкая кнопка того же чата во время генерации
    t1 = time.monotonic()
    await asyncio.wait_for(botmod.dp.feed_update(b, u_light), 5)
    light_time = time.monotonic() - t1
    check("лёгкая кнопка не ждёт генерацию следующего дня", light_time < 1.0, f"{light_time:.2f} с")
    check("на «приготовлено» Telegram ответил сразу", answered(s, u_done.callback_query.id, 0.5))
    check("человек сразу видит «готовлю следующий день»", any(n == "EditMessageText" and "Готовлю" in d.get("text", "") and t < 0.5
                                                             for t, n, d in s.calls), str([(round(t, 2), n) for t, n, d in s.calls]))
    u_again = cb("weekmenu:done:0")          # нетерпеливое повторное нажатие
    await botmod.dp.feed_update(b, u_again)
    check("повторное нажатие во время генерации: «уже готовлю», без второй генерации",
          any(n == "AnswerCallbackQuery" and "Уже готовлю" in d.get("text", "") for t, n, d in s.calls))
    await task
    st = week_menu._load(C)
    check("день 2 собран один раз", "2" in st["days"] and st["history_titles"].count("Омлет") == 1, str(st["history_titles"]))

    # открыть слот ещё не собранного дня: ответ на нажатие и «готовлю» — сразу
    s.calls.clear()
    T0[0] = time.monotonic()
    u_slot = cb("weekmenu:slot:2")
    await botmod.dp.feed_update(b, u_slot)
    check("слот несобранного дня: ответ на нажатие сразу", answered(s, u_slot.callback_query.id, 0.5))
    check("слот несобранного дня: «готовлю» сразу, потом блюдо", any("Готовлю" in d.get("text", "") and t < 0.5 for t, n, d in s.calls if n == "EditMessageText")
          and any("Омлет" in d.get("text", "") for t, n, d in s.calls if n == "EditMessageText"))
    # уже собранный день открывается без «готовлю»
    s.calls.clear()
    await botmod.dp.feed_update(b, cb("weekmenu:slot:0"))
    check("собранный день открывается сразу, без «готовлю»", not any("Готовлю" in d.get("text", "") for t, n, d in s.calls))


asyncio.run(main())
check("все кнопки с ИИ и сборкой дня — вне общей очереди чата", all(p in tg_safety.HEAVY_PREFIXES for p in ("weekmenu:done:", "dy:ask")))

# сайт: день уже собран — попытка не списывается; техническая ошибка не показывается
import web_api
from fastapi.testclient import TestClient

cl = TestClient(web_api.app)
dev = cl.post("/api/device").json()["device"]
dev_id = web_api.web_db.device_id(dev)
accounts._q("INSERT INTO links(dev_id, tg_id, ts) VALUES(?,?,?)", (dev_id, C, time.time()))
check("человеческий текст ошибки: свой", web_api._human_error(RuntimeError("План питания не найден")) == "План питания не найден")
check("техническая ошибка ИИ не показывается", "deepseek" not in web_api._human_error(RuntimeError("deepseek: Client error '401 Unauthorized' for url 'https://api'")))
check("ошибка сети не показывается", "ReadTimeout" not in web_api._human_error(TimeoutError("ReadTimeout")))

# первый заход на сайт: профиль приходит вместе с устройством, справочник грузится параллельно
j = cl.post("/api/device").json()
me = cl.get("/api/me", headers={"X-Device": j["device"]}).json()
check("новое устройство сразу с профилем (тот же, что /api/me)", isinstance(j.get("me"), dict) and set(j["me"]) == set(me), str(set(me) ^ set(j.get("me") or {}))[:200])
idx = open("web/index.html", encoding="utf-8").read()
check("сайт: устройство и справочник запрашиваются одновременно, без лишнего /api/me", "Promise.all([ensureDevice(),pMeta])" in idx and "ME_FRESH&&ME" in idx)

m = cl.get("/api/meta").json()
check("справочник говорит, где есть реклама; без баннеров сайт её не запрашивает", m.get("ad_slots") == [] and "META.ad_slots.indexOf(slot)<0" in idx, str(m.get("ad_slots")))

print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
