"""Справка с разделами, тексты про магазины, «Написать разработчику» (бот, сайт, админка)."""
import asyncio
import os
import sys
from datetime import datetime

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_hp.db", "accounts_hp.db", "accounts_hp.db-wal", "accounts_hp.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_hp.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_hp.db"
os.environ["WEB_DATA_DIR"] = "web_data_hp"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Update, CallbackQuery, Message, Chat, User

import config
config.ADMIN_PASSWORD, config.PARTNER_PASSWORD, config.WEB_SECRET = "owner-pass-1", "partner-pass-1", "secret-x"
config.ADMIN_TG_IDS = [555000777]
import accounts
import feedback
import helpdata
import shopping
import bot as botmod
from states import FeedbackForm

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


# ---------- тексты справки ----------
titles = [t for _, t, _ in helpdata.SECTIONS]
check("в справке 11 разделов с уникальными названиями", len(titles) == 11 and len(set(titles)) == 11, str(titles))
shops = helpdata.SECTION_BY_KEY["shops"][1]
check("про магазины: ВкусВилл — корзина одной кнопкой", "ВкусВилл: корзина одной кнопкой" in shops)
check("про магазины: остальные в перспективе, пока поиск магазина", "в перспективе" in shops and "откроется его поиск" in shops and "положить нужный товар в корзину для доставки" in shops, shops)
check("про магазины: войти в браузере в профиль магазина", "войдите в браузере в свой профиль магазина" in shops)
check("справка говорит про тариф «Повар» и пробную неделю", "«Повар»" in helpdata.SECTION_BY_KEY["plan"][1] and "7 дней бесплатно" in helpdata.SECTION_BY_KEY["plan"][1])
check("справка без старых ссылок и длинных разделов",
      "pump-um" not in helpdata.INTRO and all(len(t) < 1800 for _, _, t in helpdata.SECTIONS))
note = shopping.format_store_links(["молоко"], "fiveka")
check("список в боте: подсказка войти в браузере в профиль магазина", "войдите в браузере в свой профиль магазина" in note, note[-200:])


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls, self.n = [], 0

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append((name, method.model_dump(exclude_none=True)))
        if name in ("SendMessage", "EditMessageText", "SendPhoto"):
            self.n += 1
            return Message(message_id=9300 + self.n, date=datetime.now(), chat=Chat(id=C, type="private"),
                           text=getattr(method, "text", "") or "").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


C = 880020
user = User(id=C, is_bot=False, first_name="T", username="tester")
chat = Chat(id=C, type="private")
seq = [0]


def cb(data, msg_id=600):
    seq[0] += 1
    m = Message(message_id=msg_id, date=datetime.now(), chat=chat, text="x")
    return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=user, chat_instance="x", message=m, data=data))


def msg(text):
    seq[0] += 1
    return Update(update_id=seq[0], message=Message(message_id=100 + seq[0], date=datetime.now(), chat=chat, from_user=user, text=text))


def btns(p):
    return [b["text"] for r in (p or {}).get("reply_markup", {}).get("inline_keyboard", []) for b in r]


def last_txt(s):
    return next((p for n, p in reversed(s.calls) if n in ("SendMessage", "EditMessageText")), None)


def last(s, name):
    return next((p for n, p in reversed(s.calls) if n == name), None)


async def main():
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    st = botmod.dp.fsm.get_context(b, C, C)

    await botmod.dp.feed_update(b, msg("/help"))
    m = last(s, "SendMessage")
    check("/help: все разделы кнопками и «Написать разработчику»", set(titles) <= set(btns(m)) and "✉️ Написать разработчику" in btns(m), str(btns(m)))
    await botmod.dp.feed_update(b, cb("help:s:shops"))
    m = last(s, "EditMessageText")
    check("раздел про магазины открывается на месте", m and "ВкусВилл: корзина одной кнопкой" in m["text"] and "⬅️ К разделам справки" in btns(m), str(m)[:200])
    await botmod.dp.feed_update(b, cb("help:hub"))
    check("назад к разделам", set(titles) <= set(btns(last(s, "EditMessageText"))))
    lens = []
    for key, _t, _x in helpdata.SECTIONS:
        n0 = len(s.calls)
        await botmod.dp.feed_update(b, cb(f"help:s:{key}"))
        sent_msgs = [p for n, p in s.calls[n0:] if n == "EditMessageText"]
        lens.append(len(sent_msgs[-1]["text"]) if sent_msgs else -1)
    check("каждый раздел справки открывается без ошибки и короче лимита Telegram (4096) с запасом", all(0 < n < 2000 for n in lens), str(lens))
    check("главная справка и команда короткие", len(helpdata.INTRO) < 700 and len(botmod.HELP_TEXT) < 700)
    await botmod.dp.feed_update(b, cb("help:s:нет-такого"))
    check("неизвестный раздел не ломает бота", last(s, "AnswerCallbackQuery") is not None)

    # написать разработчику
    await botmod.dp.feed_update(b, cb("help:fb"))
    m = last_txt(s)
    check("выбор: идея, ошибка, вопрос", {"💡 Идея", "🐞 Ошибка", "❓ Вопрос"} <= set(btns(m)), str(btns(m)))
    await botmod.dp.feed_update(b, cb("fb:k:bug"))
    check("ждём текст сообщения", await st.get_state() == FeedbackForm.text.state)
    await botmod.dp.feed_update(b, msg("до"))
    check("слишком короткое не принимается", "несколько слов" in last_txt(s)["text"] and await st.get_state() == FeedbackForm.text.state)
    await botmod.dp.feed_update(b, msg("Кнопка «Готово» не отмечает шаг"))
    items = accounts.feedback_list()
    check("сообщение сохранено с типом и источником", len(items) == 1 and items[0]["kind"] == "bug" and items[0]["src"] == "bot" and items[0]["uid"] == str(C), str(items))
    sent_admin = [p for n, p in s.calls if n == "SendMessage" and p.get("chat_id") == 555000777]
    check("владельцу ушло уведомление с текстом и автором", sent_admin and "ошибка" in sent_admin[-1]["text"] and "@tester" in sent_admin[-1]["text"]
          and "не отмечает шаг" in sent_admin[-1]["text"], str(sent_admin)[:300])
    check("человек получил подтверждение, состояние сброшено", "Спасибо" in last_txt(s)["text"] and await st.get_state() is None)
    for i in range(4):
        await botmod.dp.feed_update(b, msg(f"/feedback"))
        await botmod.dp.feed_update(b, cb("fb:k:idea"))
        await botmod.dp.feed_update(b, msg(f"Идея номер {i}"))
    await botmod.dp.feed_update(b, msg("/feedback"))
    await botmod.dp.feed_update(b, cb("fb:k:idea"))
    await botmod.dp.feed_update(b, msg("Шестая за сутки"))
    check("после 5 сообщений за сутки новые не принимаются", len(accounts.feedback_list()) == 5 and "много сообщений" in last_txt(s)["text"], str(len(accounts.feedback_list())))

asyncio.run(main())

# ---------- сайт и админка ----------
from fastapi.testclient import TestClient
import web_api
sent_web = []
feedback.notify_sync = lambda text: sent_web.append(text)       # в тестах в Telegram ничего не отправляем
with TestClient(web_api.app) as cl:
    h = cl.get("/api/help").json()
    check("сайт получает разделы справки", len(h["sections"]) == 11 and h["sections"][0]["key"] == "start")
    dev = cl.post("/api/device").json()["device"]
    hd = {"X-Device": dev}
    check("пустое сообщение отклоняется", cl.post("/api/feedback", json={"kind": "idea", "text": " "}, headers=hd).status_code == 422)
    r = cl.post("/api/feedback", json={"kind": "bug", "text": "Не открывается PDF на телефоне", "contact": "@me"}, headers=hd)
    check("сообщение с сайта принято", r.status_code == 200 and sent_web and "Не открывается PDF" in sent_web[-1] and "@me" in sent_web[-1], r.text)
    for i in range(4):
        cl.post("/api/feedback", json={"kind": "idea", "text": f"Идея {i} для сайта"}, headers=hd)
    r = cl.post("/api/feedback", json={"kind": "idea", "text": "Ещё одна идея"}, headers=hd)
    check("лимит 5 сообщений в сутки на сайте — 429", r.status_code == 429, r.text)
    html = cl.get("/").text
    check("страница сайта: вкладка «Справка», выпадающие разделы, форма, подсказка о магазинах",
          'data-tab="help"' in html and "viewHelp" in html and "data-fb-send" in html and "storenote" in html)
    cl.post("/admin/login", json={"password": "partner-pass-1"})
    check("партнёр не видит сообщения людей", cl.get("/admin/api/feedback").status_code == 403)
    cl.post("/admin/logout")
    cl.cookies.clear()
    cl.post("/admin/login", json={"password": "owner-pass-1"})
    j = cl.get("/admin/api/feedback").json()
    check("владелец видит сообщения из бота и с сайта", len(j["items"]) == 10 and {x["src"] for x in j["items"]} == {"bot", "web"}, str(len(j["items"])))
    fid = j["items"][0]["id"]
    cl.post(f"/admin/api/feedback/{fid}/done")
    j = cl.get("/admin/api/feedback").json()
    check("«Прочитано» отмечает сообщение и опускает вниз", [x["done"] for x in j["items"]].count(True) == 1 and j["items"][-1]["done"] is True)
    check("админка: вкладка «Сообщения»", "Сообщения" in cl.get("/admin").text and "loadMsgs" in cl.get("/admin").text)

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
