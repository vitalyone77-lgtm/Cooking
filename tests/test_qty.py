"""Количество в дневнике (штуки, ложки, «2 яйца») в боте и на сайте; мелочи: докупить к дню, обновление списка, фильтры книги, возврат звёзд."""
import asyncio
import os
import sys
import time
from datetime import datetime as dt

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_qt.db", "accounts_qt.db", "accounts_qt.db-wal", "accounts_qt.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_qt.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_qt.db"
os.environ["WEB_DATA_DIR"] = "web_data_qt"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from fastapi.testclient import TestClient

import tg_safety
tg_safety.FloodGuard.__init__.__defaults__ = (100000, 10)
import accounts
import bot as botmod
import config
import foods
import shoplists
import web_api

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


B = foods.BY_ID
pq = lambda q, fid: foods.parse_qty(q, B[fid])
check("яйца: число это штуки", pq("2", "yaitso")["grams"] == 110 and pq("2", "yaitso")["desc"] == "2 шт")
check("словами: «две»", pq("две", "yaitso")["grams"] == 110)
check("хлопья: ложки с горкой", pq("3 ложки", "hlopya")["grams"] == 45 and "с горкой" in pq("3 ложки", "hlopya")["desc"])
check("сахар: чайная и столовая с горкой", pq("1 ч.л.", "sahar")["grams"] == 5 and pq("2 ложки с горкой", "sahar")["grams"] == 50)
check("каша: большое число это граммы", pq("150", "grechka")["grams"] == 150 and pq("150", "grechka")["unit"] is None)
check("каша: ложками", pq("3 ложки", "grechka")["grams"] == 90)
check("бутерброд штуками", pq("2", "buterbrod")["grams"] == 240)
check("колбаса: ломтики из подписи «2 ломтика 40 г»", pq("3", "kolbasa")["grams"] == 60)
check("пол авокадо", pq("пол", "avokado")["grams"] == 100 and pq("пол", "avokado")["desc"] == "0,5 шт")
check("молоко штуками не меряют", pq("2 шт", "moloko") is None and pq("200 мл", "moloko")["grams"] == 200)
check("килограммы", pq("0,2 кг", "tvorog")["grams"] == 200)
check("масло: ложка 17 г, а не 10", foods.units(B["maslo_podsol"])[0]["grams"] == 17)
check("бессмыслица не принимается", pq("много", "yaitso") is None)
check("поиск с количеством спереди и сзади", foods.split_qty("2 яйца") == ("яйца", "2") and foods.split_qty("овсянка 3 ложки с горкой") == ("овсянка", "3 ложки")
      and foods.split_qty("две котлеты") == ("котлеты", "две"))
check("проценты в названии не количество", foods.split_qty("молоко 2,5%") == ("молоко 2,5%", ""))
check("без количества запрос не меняется", foods.split_qty("борщ") == ("борщ", ""))
check("запись в дневнике", foods.entry_title(B["yaitso"], 110, "2 шт") == "Яйцо варёное (2 шт, 110 г)")

C = 796001
seq = [0]
user, chat = User(id=C, is_bot=False, first_name="T"), Chat(id=C, type="private")
config.ADMIN_TG_IDS = [C]


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls, self.n, self.refund_fail = [], 0, False

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append((name, method.model_dump(exclude_none=True)))
        if name == "RefundStarPayment" and self.refund_fail:
            raise RuntimeError("CHARGE_ALREADY_REFUNDED")
        if name in ("SendMessage", "EditMessageText", "SendPhoto"):
            self.n += 1
            return Message(message_id=9800 + self.n, date=dt.now(), chat=chat, text=getattr(method, "text", "") or "x").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


def cb(data, text="x"):
    seq[0] += 1
    m = Message(message_id=820, date=dt.now(), chat=chat, text=text)
    return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=user, chat_instance="x", message=m, data=data))


def msg(text):
    seq[0] += 1
    return Update(update_id=seq[0], message=Message(message_id=200 + seq[0], date=dt.now(), chat=chat, from_user=user, text=text))


def datas(p):
    return [x.get("callback_data") for r in (p or {}).get("reply_markup", {}).get("inline_keyboard", []) for x in r]


def last_text(s):
    return next((p for n, p in reversed(s.calls) if n in ("SendMessage", "EditMessageText", "SendPhoto", "EditMessageCaption")), {})


async def main():
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    accounts.consent_set(C)
    accounts.goals_set(C, feats=["diary"])
    today = lambda: [i["title"] for i in accounts.diary_day(C)["items"]]

    await botmod.dp.feed_update(b, cb("dy:f:yaitso"))
    p = last_text(s)
    check("яйцо: кнопки 1–4 шт, без «Малая/Большая»", {"dy:u:yaitso:pc:1", "dy:u:yaitso:pc:4"} <= set(datas(p)) and not any(d and d.startswith("dy:p:") for d in datas(p)), str(datas(p)))
    await botmod.dp.feed_update(b, cb("dy:u:yaitso:pc:2"))
    check("записано 2 шт", "Яйцо варёное (2 шт, 110 г)" in today(), str(today()))
    await botmod.dp.feed_update(b, cb("dy:f:hlopya"))
    p = last_text(s)
    check("хлопья: ложки с горкой кнопками и порции", "dy:u:hlopya:tb:3" in datas(p) and "dy:p:hlopya:10" in datas(p) and "с горкой" in p.get("text", ""), str(datas(p)))
    await botmod.dp.feed_update(b, cb("dy:g:hlopya"))
    check("своё количество: подсказка про ложки", "ложк" in last_text(s).get("text", "") or "ст. л." in last_text(s).get("text", ""))
    await botmod.dp.feed_update(b, msg("3 ложки"))
    check("текстом «3 ложки»", any(t.startswith("Овсяные хлопья сухие (3 ст. л. с горкой, 45 г)") for t in today()), str(today()))
    await botmod.dp.feed_update(b, cb("dy:g:banan"))
    await botmod.dp.feed_update(b, msg("много"))
    check("непонятное количество: просим ещё раз, ничего не пишем", "не понял" in last_text(s).get("text", "") and not any("Банан" in t for t in today()))
    await botmod.dp.feed_update(b, msg("150 г"))
    check("граммами по-прежнему", "Банан (150 г)" in today(), str(today()))

    await botmod.dp.feed_update(b, cb("dy:find"))
    await botmod.dp.feed_update(b, msg("2 яйца"))
    p = last_text(s)
    check("поиск «2 яйца» находит яйца", "dy:f:yaitso" in datas(p), str(datas(p)))
    await botmod.dp.feed_update(b, cb("dy:f:yaitso"))
    p = last_text(s)
    check("в продукте первой кнопкой готовая запись «2 шт»", datas(p)[0] == "dy:u:yaitso:pc:2" and "Вы написали: 2 шт" in p.get("text", ""), str(datas(p)[:2]))
    await botmod.dp.feed_update(b, cb("dy:cat"))
    await botmod.dp.feed_update(b, cb("dy:f:yaitso"))
    check("после выбора из разделов старое количество не подставляется", not last_text(s).get("text", "").startswith("Вы") and "Вы написали" not in last_text(s).get("text", ""))

    # мелочь: докупить к дню
    import week_menu
    week_menu._save(C, {"days_total": 3, "current_day": 2, "meal_keys": ["lunch"], "days": {"1": {"shopping_terms": ["курица"], "meals": {"lunch": {"text": "🍽 Суп\n\n*Продукты:*\n- Курица — 300 г\n- Яйцо — 2 шт\n\n*Инструкция:*\n1. Варите."}}}}})
    check("докупить к дню: с граммовкой", botmod.resolve_shopping_amounts(Message(message_id=1, date=dt.now(), chat=chat, text="x"), "wd1").get("курица") == 300)

    # мелочь: книга
    accounts.sess_set(botmod.bot_book._key(C), {"appliance": "oven", "q": "курица", "meal": "ужин"})
    await botmod.dp.feed_update(b, cb("bk:m:завтрак"))
    f = botmod.bot_book._flt(C)
    check("книга: раздел меняется, условия остаются", f.get("meal") == "завтрак" and f.get("appliance") == "oven" and f.get("q") == "курица", str(f))
    await botmod.dp.feed_update(b, cb("bk:m:all"))
    check("книга: «все» убирает только раздел", "meal" not in botmod.bot_book._flt(C) and botmod.bot_book._flt(C).get("appliance") == "oven")

    # мелочь: возврат звёзд
    accounts.apply_payment("stxVERYLONGCHARGEIDFROMTELEGRAM_0123456789abcdefABCDEF0123456789", C, 275, "XTR", 30, "month")
    await botmod.dp.feed_update(b, msg("/refund stxVERYLONGCHARGEIDFROMTELEGRAM_0123456789abcdefABCDEF0123456789"))
    p = last_text(s)
    go = next(d for d in datas(p) if d.startswith("rf:go:"))
    check("возврат звёзд: кнопка короткая, текст про звёзды", len(go.encode()) <= 64 and "вернуть звёзды" in str(p), str(p)[:300])
    s.refund_fail = True
    await botmod.dp.feed_update(b, cb(go))
    check("Telegram отказал: ничего не отмечено", "не вернул" in last_text(s).get("text", "") and accounts.payment_by_op("stxVERYLONGCHARGEIDFROMTELEGRAM_0123456789abcdefABCDEF0123456789")["currency"] == "XTR")
    s.refund_fail = False
    await botmod.dp.feed_update(b, msg("/refund stxVERYLONGCHARGEIDFROMTELEGRAM_0123456789abcdefABCDEF0123456789"))
    go = next(d for d in datas(last_text(s)) if d.startswith("rf:go:"))
    await botmod.dp.feed_update(b, cb(go))
    rp = [c for n, c in s.calls if n == "RefundStarPayment"][-1]
    check("звёзды возвращены через Telegram и отмечены", rp["user_id"] == C and rp["telegram_payment_charge_id"].startswith("stxVERY") and
          accounts.payment_by_op("stxVERYLONGCHARGEIDFROMTELEGRAM_0123456789abcdefABCDEF0123456789")["currency"] == "XTRREFUND" and "275 ⭐" in last_text(s).get("text", ""), str(last_text(s))[:200])
    check("возвращённые звёзды не в выручке", accounts.money_stats()["total_stars"] == 0)


asyncio.run(main())

# мелочь: повторная отправка списка
lid = shoplists.create(C, "Суп", [{"name": "курица"}, {"name": "рис"}])
check("отметки обновляются на той же странице", shoplists.set_done(lid, [1]) and shoplists.get(lid)["done"] == [1])

# сайт
cl = TestClient(web_api.app)
dev = cl.post("/api/device").json()["device"]
hdr = {"X-Device": dev}
accounts._q("INSERT INTO links(dev_id, tg_id, ts) VALUES(?,?,?)", (web_api.web_db.device_id(dev), C, time.time()))
r = cl.get("/api/foods", params={"q": "2 яйца", "ai": 0}, headers=hdr).json()
eg = next((i for i in r["items"] if i["id"] == "yaitso"), None)
check("сайт: поиск «2 яйца» с готовой записью", eg and eg["ask"]["title"] == "2 шт" and eg["ask"]["grams"] == 110 and eg["units"][0]["key"] == "pc", str(r)[:300])
r = cl.post("/api/diary", json={"food": "hlopya", "qty": "2 ложки"}, headers=hdr)
check("сайт: запись «2 ложки»", r.status_code == 200 and any(t.startswith("Овсяные хлопья сухие (2 ст. л. с горкой, 30 г)") for t in [i["title"] for i in accounts.diary_day(C)["items"]]), r.text)
r = cl.post("/api/diary", json={"food": "moloko", "qty": "2 шт"}, headers=hdr)
check("сайт: молоко штуками понятной ошибкой", r.status_code == 400 and "количество" in r.text)
r = cl.post("/api/diary", json={"food": "banan", "grams": 120}, headers=hdr)
check("сайт: граммами как раньше", r.status_code == 200)
idx = open("web/index.html", encoding="utf-8").read()
check("сайт: кнопки штук и поле «2 шт, 3 ложки»", "data-q=" in idx and "2 шт, 3 ложки, 180 г" in idx)

foods_text = "🍽 Суп\n\n👥 Порций: 4\n\n*Продукты:*\n- Курица — 600 г\n- Рис — 100 г\n\n*Инструкция:*\n1. Варите."
r = cl.post("/api/shopping", json={"text": foods_text}, headers=hdr)
check("сайт: продукты и магазины для рецепта из книги, с количеством", r.status_code == 200 and any(i["name"].lower() == "курица" and i.get("amount") == "600 г" and i["links"] for i in r.json()["items"]), r.text[:300])
check("сайт: в книге порции и кнопка продуктов", "bookBody" in idx and "data-shop-load" in idx)

print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
