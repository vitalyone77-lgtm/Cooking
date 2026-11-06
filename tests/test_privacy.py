"""Нет персональных данных в базе, «как в прошлый раз» для текстовых полей, IP не копятся, ссылки блогеров."""
import asyncio
import os
import sqlite3
import sys
import time
from datetime import datetime

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_pr.db", "accounts_pr.db", "accounts_pr.db-wal", "accounts_pr.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_pr.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_pr.db"
os.environ["WEB_DATA_DIR"] = "web_data_pr"

# «старая» база с профилем: аллергии должны исчезнуть из файла
old = sqlite3.connect(PROJ + "/accounts_pr.db")
old.execute("PRAGMA journal_mode=WAL")
old.execute("CREATE TABLE profiles(uid TEXT PRIMARY KEY, allergies TEXT DEFAULT '', never TEXT DEFAULT '', updated REAL)")
old.execute("INSERT INTO profiles VALUES('42', 'мариновонедопустимыйорех', 'секретнаякинза', 1)")
old.commit()
old.close()

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Update, CallbackQuery, Message, Chat, User

import config
config.ADMIN_PASSWORD, config.PARTNER_PASSWORD, config.WEB_SECRET = "owner-pass-1", "partner-pass-1", "secret-x"
import accounts
import storage
import usage
import profile_ctx
import bot as botmod
from states import RecipeForm, DayMenuForm

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


# ---------- база ----------
accounts.settings_all()      # первое обращение запускает миграцию
con = sqlite3.connect(PROJ + "/accounts_pr.db")
tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
con.close()
raw = open(PROJ + "/accounts_pr.db", "rb").read()
wal = open(PROJ + "/accounts_pr.db-wal", "rb").read() if os.path.exists(PROJ + "/accounts_pr.db-wal") else b""
check("таблица профилей удалена", "profiles" not in tables, str(tables))
check("аллергии и «не использовать» стёрты из файла базы", "мариновонедопустимыйорех".encode() not in raw + wal
      and "секретнаякинза".encode() not in raw + wal)
check("нужное для подписок осталось", {"subs", "payments", "links", "refs", "promo_used"} <= tables)
check("статистика аккаунтов без профилей", "profiles" not in accounts.account_stats())
check("функций профиля в коде нет", not hasattr(accounts, "get_profile") and not hasattr(accounts, "set_profile"))

# ---------- IP ----------
usage._q("INSERT INTO gens(src, uid, kind, ts, ip) VALUES('web','w1','recipe',?,'1.2.3.4')", (time.time() - 5 * 86400,))
usage._q("INSERT INTO gens(src, uid, kind, ts, ip) VALUES('web','w2','recipe',?,'5.6.7.8')", (time.time() - 60,))
usage._ips_purged = 0.0
usage.try_spend("w3", "recipe", "web", ip="9.9.9.9")
ips = {r[0] for r in usage._rows("SELECT ip FROM gens WHERE ip IS NOT NULL")}
check("старые IP обезличены, сегодняшние (для лимита с адреса) остались", "1.2.3.4" not in ips and "5.6.7.8" in ips and "9.9.9.9" in ips, str(ips))

# ---------- последний ввод ----------
C = 777000303
storage.save_last_inputs(C, {"preferred": "курица, рис", "excluded": "орехи", "macro_goal": ""})
check("прошлый ввод запомнился (пустые поля не сохраняются)", storage.get_last_inputs(C) == {"preferred": "курица, рис", "excluded": "орехи"}, str(storage.get_last_inputs(C)))
storage.save_last_inputs(C, {"preferred": "", "excluded": "", "macro_goal": "500 ккал"})
check("пропуск не стирает прошлое, новое дописывается", storage.get_last_inputs(C) == {"preferred": "курица, рис", "excluded": "орехи", "macro_goal": "500 ккал"})
storage.save_last_request(C, {"cuisine": "classic", "preferred": "х", "excluded": "у"}, "Блюдо")
check("новый запрос не затирает прошлый ввод", storage.get_last_inputs(C).get("excluded") == "орехи")
storage._update(C, inputs={"preferred": "старое", "ts": time.time() - 100 * 86400})
check("через 90 дней прошлый ввод забывается", storage.get_last_inputs(C) == {})
check("при старте старый ввод стирается с диска", storage.purge_old_inputs() >= 1 and "inputs" not in (storage.get_last_request(C) or {}))
storage.save_last_inputs(C, {"preferred": "курица, рис", "excluded": "орехи"})


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
            return Message(message_id=9000 + self.n, date=datetime.now(), chat=Chat(id=C, type="private"),
                           text=getattr(method, "text", "") or "").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


user = User(id=C, is_bot=False, first_name="T")
chat = Chat(id=C, type="private")
seq = [0]


def cb(data, msg_id=600):
    seq[0] += 1
    m = Message(message_id=msg_id, date=datetime.now(), chat=chat, text="x")
    return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=user, chat_instance="x", message=m, data=data))


def btns(p):
    return [b["text"] for r in (p or {}).get("reply_markup", {}).get("inline_keyboard", []) for b in r]


def datas(p):
    return [b["callback_data"] for r in (p or {}).get("reply_markup", {}).get("inline_keyboard", []) for b in r]


def last(s, name):
    return next((p for n, p in reversed(s.calls) if n == name), None)


async def main():
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    st = botmod.dp.fsm.get_context(b, C, C)
    msg = Message(message_id=1, date=datetime.now(), chat=chat, text="x").as_(b)

    # рецепт: шаг «что использовать»
    await st.clear()
    await botmod.ask_preferred(msg, st)
    p = last(s, "SendMessage")
    check("рецепт: на шаге продуктов есть «Как в прошлый раз» и «Пропустить»", "↩️ Как в прошлый раз: курица, рис" in btns(p) and "➡️ Пропустить" in btns(p) and "reuse:preferred" in datas(p), str(btns(p)))
    await botmod.dp.feed_update(b, cb("reuse:preferred"))
    d = await st.get_data()
    check("нажали — продукты подставились, дальше «исключить»", d.get("preferred") == "курица, рис" and await st.get_state() == RecipeForm.excluded.state, str(d))
    p = last(s, "SendMessage")
    check("на шаге «исключить» тоже есть прошлый ввод", "↩️ Как в прошлый раз: орехи" in btns(p), str(btns(p)))
    await botmod.dp.feed_update(b, cb("reuse:excluded"))
    d = await st.get_data()
    check("исключения подставились, дальше время", d.get("excluded") == "орехи" and await st.get_state() == RecipeForm.time.state, str(d))
    n0 = len(s.calls)
    await botmod.dp.feed_update(b, cb("reuse:preferred"))
    d = await st.get_data()
    check("старая кнопка в другом шаге ничего не ломает", d.get("excluded") == "орехи" and await st.get_state() == RecipeForm.time.state)

    # меню на день
    await st.clear()
    await st.set_state(DayMenuForm.preferred)
    await botmod.dp.feed_update(b, cb("reuse:dm_preferred"))
    d = await st.get_data()
    check("меню на день: «как в прошлый раз» работает", d.get("preferred") == "курица, рис" and await st.get_state() == DayMenuForm.excluded.state, str(d))

    # цель КБЖУ
    await st.clear()
    storage.save_last_inputs(C, {"macro_goal": "500 ккал, белки 40 г"})
    await botmod.dp.feed_update(b, cb("cuisine:macro_goal"))
    await st.set_state(RecipeForm.cuisine)
    await botmod.dp.feed_update(b, cb("cuisine:macro_goal"))
    p = last(s, "SendMessage")
    check("цель КБЖУ: кнопка с прошлым вводом", any("500 ккал" in t for t in btns(p)), str(btns(p)))

    # профиль без аллергий
    await botmod.dp.feed_update(b, cb("profile:open", 800))
    p = last(s, "SendMessage")
    check("профиль: нет полей аллергий", "Аллергии" not in " ".join(btns(p)) and "pf:edit:allergies" not in datas(p), str(btns(p)))
    check("профиль: подсказка про «Исключить» и кнопка согласия на данные", "Исключить" in p["text"] and "✅ Согласен" in btns(p), str(btns(p)))

    # запрос исключения доходит до ИИ
    profile_ctx.set_avoid("")
    check("пустые исключения — проверка выключена", profile_ctx.current.get() is None)

asyncio.run(main())

# ---------- сайт и админка ----------
from fastapi.testclient import TestClient
import web_api
with TestClient(web_api.app) as cl:
    html = cl.get("/").text
    check("сайт: профиль с аллергиями убран, прошлый ввод помним на устройстве", "pf-all" not in html and "saveForm" in html and "data-consent" in html)
    dev = cl.post("/api/device").json()["device"]
    check("сайт: /api/me не отдаёт профиль", "profile" not in cl.get("/api/me", headers={"X-Device": dev}).json())
    cl.post("/admin/login", json={"password": "partner-pass-1"})
    page = cl.get("/admin").text
    check("админка: генератор ссылок блогеров и защита партнёра", "Создать ссылку" in page and "lockForPartner" in page and "profiles" not in page)
    accounts.set_ref(1001, "anna")
    refs = cl.get("/admin/api/stats").json()["refs"]
    check("метка из ссылки t.me/бот?start=ref_anna появляется в статистике сама", any(r["ref"] == "anna" and r["users"] == 1 for r in refs), str(refs))
    check("метка очищается от лишних символов", (accounts.set_ref(1002, "bad ref!") or True)
          and any(r["ref"] == "badref" for r in cl.get("/admin/api/stats").json()["refs"]))

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
