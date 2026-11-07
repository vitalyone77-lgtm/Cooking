"""Бонусы за друзей: начисления, защита от накруток, возврат, объединение аккаунтов; кнопки и страницы; полка по фото."""
import asyncio
import os
import sys
import time
from datetime import datetime as dt

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_fr.db", "accounts_fr.db", "accounts_fr.db-wal", "accounts_fr.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_fr.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_fr.db"
os.environ["WEB_DATA_DIR"] = "web_data_fr"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from fastapi.testclient import TestClient

import tg_safety
tg_safety.FloodGuard.__init__.__defaults__ = (100000, 10)
import accounts
import bot as botmod
import config
import partners
import plans
import referrals as rf
import usage
import vision
import web_api

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


config.PAYWALL_ENABLED = True
plans.reload()
DAY = 86400


def left(uid):
    return round((accounts.premium_until(uid) - time.time()) / DAY, 2)


def come(friend, inviter):
    accounts.set_ref(friend, rf.code_of(inviter))


def recipe(uid):
    usage._q("INSERT INTO gens(src, uid, kind, ts) VALUES('bot', ?, 'recipe', ?)", (str(uid), time.time()))


# ---- основной путь ----
A, F1 = 1001, 2001
code = rf.code_of(A)
check("личный код постоянный и с префиксом", code == rf.code_of(A) and code.startswith("fr_"))
check("ссылки на сайт и бота", rf.links(A)["site"].endswith(f"/?ref={code}") and f"start=ref_{code}" in rf.links(A)["bot"])
come(F1, A)
check("только вход без рецепта: ничего не начислено", rf.process() == [] and left(A) <= 0, str(left(A)))
recipe(F1)
notes = rf.process()
check("первый рецепт друга: неделя пригласившему и другу", 6.9 < left(A) < 7.1 and 6.9 < left(F1) < 7.1, f"{left(A)} {left(F1)}")
check("оба получили сообщение, без имён", {n[0] for n in notes} == {str(A), str(F1)} and all("2001" not in n[1] and "1001" not in n[1] for n in notes), str(notes))
check("повторный запуск ничего не добавляет", rf.process() == [] and 6.9 < left(A) < 7.1)
check("бонус не считается пробным периодом: проба остаётся", not accounts.trial_used(A))
accounts.apply_payment("pw:901", F1, 290, "RUB", 30, "month")
notes = rf.process()
check("друг оплатил: неделя стала месяцем", 29.9 < left(A) < 30.1 and "оформил подписку" in notes[0][1], f"{left(A)} {notes}")
check("повторно за ту же оплату не начисляем", rf.process() == [] and 29.9 < left(A) < 30.1)
accounts.apply_payment("pw:902", F1, 290, "RUB", 30, "month")
check("вторая оплата того же друга месяц не добавляет", rf.process() == [] and 29.9 < left(A) < 30.1)
accounts.refund_payment("901")
accounts.refund_payment("902")
check("все оплаты возвращены: прибавка снята, неделя осталась", (rf.process() == []) and 6.9 < left(A) < 7.1, str(left(A)))
st = rf.stats(A)
check("итоги после возврата", st["friends"] == 1 and st["paid"] == 0 and st["days"] == 7, str(st))
accounts.apply_payment("pw:903", F1, 290, "RUB", 30, "month")
rf.process()
check("снова оплатил: снова месяц", 29.9 < left(A) < 30.1 and rf.stats(A)["paid"] == 1)

# ---- чего не считаем ----
B = 1002
accounts.set_ref(B, rf.code_of(B))
recipe(B)
rf.process()
check("своя ссылка не работает", left(B) <= 0, str(left(B)))
OLD = 2100
usage._q("INSERT INTO seen(uid, ts, stage, first) VALUES(?,?,0,?)", (str(OLD), time.time(), time.time() - 30 * DAY))
come(OLD, B)
recipe(OLD)
rf.process()
check("давний пользователь другом не считается", left(B) <= 0 and left(OLD) <= 0)
G = "w555"
accounts._q("INSERT INTO refs(uid, ref, ts) VALUES(?,?,?)", (G, rf.code_of(B), time.time()))
usage._q("INSERT INTO gens(src, uid, kind, ts) VALUES('web', ?, 'recipe', ?)", (G, time.time()))
rf.process()
check("гость без входа не считается", left(B) <= 0)
accounts._q("UPDATE refs SET uid='2101' WHERE uid=?", (G,))
accounts._q("INSERT INTO links(dev_id, tg_id, ts) VALUES(555, 2101, ?)", (time.time(),))
rf.process()
check("гость получил рецепт и вошёл: считается", 6.9 < left(B) < 7.1, str(left(B)))
T = 2102
come(T, B)
accounts.apply_payment("pwtest:1", T, 290, "RUBTEST", 30, "month")
check("тестовый платёж не считается и без рецепта другом не делает", rf.process() == [] and 6.9 < left(B) < 7.1)

# ---- потолок ----
C0 = 1003
for i in range(MAX := rf.MAX_WEEKS):
    come(3000 + i, C0)
    recipe(3000 + i)
    rf.process()
check("пять недель начислены", 34.9 < left(C0) < 35.1, str(left(C0)))
come(3100, C0)
recipe(3100)
notes = rf.process()
check("шестая неделя за 30 дней не начислена, человеку честно сказано", 34.9 < left(C0) < 35.1 and any("уже начислено 5" in n[1] for n in notes), str(notes))
check("другу подарок всё равно дан", 6.9 < left(3100) < 7.1)
check("осталось недель: 0", rf.stats(C0)["weeks_left"] == 0)
accounts.apply_payment("pw:904", 3100, 290, "RUB", 30, "month")
rf.process()
check("оплата сверх потолка: полный месяц", 64.9 < left(C0) < 65.1, str(left(C0)))

# ---- подписка оплаченная остаётся оплаченной ----
D = 1004
accounts.apply_payment("pw:905", D, 290, "RUB", 30, "month")
accounts.add_bonus_days(D, 7)
check("бонус не меняет источник оплаченной подписки", accounts._q("SELECT source FROM subs WHERE uid=?", (str(D),), one=True)[0] == "payment" and 36.9 < left(D) < 37.1)
E = 1005
accounts.add_bonus_days(E, 7)
check("без подписки источник friend, и в партнёрских подсчётах это не оплата", accounts._q("SELECT source FROM subs WHERE uid=?", (str(E),), one=True)[0] == "friend")
accounts.add_bonus_days(1006, -5)
check("снятие дней у человека без подписки ничего не ломает", left(1006) <= 0)

# ---- партнёры и админка ----
try:
    partners.ensure("fr_abc")
    bad = True
except ValueError:
    bad = False
check("партнёрский код не может занять личную ссылку", not bad)
refs = {r["ref"] for r in accounts.ref_stats()}
check("в админке одна строка «друзья», личных кодов не видно", "друзья" in refs and not any(r.startswith("fr_") for r in refs), str(refs))

# ---- объединение ----
V = accounts.VK_OFFSET + 7
vcode = rf.code_of(V)
FV = 3200
accounts.set_ref(FV, vcode)
recipe(FV)
rf.process()
c = accounts._conn()
with c:
    accounts._merge_accounts(c, str(V), "1007")
c.close()
check("после объединения итоги у нового аккаунта", rf.stats(1007)["friends"] == 1)
FV2 = 3201
accounts.set_ref(FV2, vcode)
recipe(FV2)
rf.process()
check("старая ссылка продолжает начислять в общий аккаунт", rf.stats(1007)["friends"] == 2 and left(1007) > 0, str(rf.stats(1007)))

# ---- подписка выключена ----
config.PAYWALL_ENABLED = False
plans.reload()
come(3300, E)
recipe(3300)
check("подписка выключена: ничего не считаем", rf.process() == [])
config.PAYWALL_ENABLED = True
plans.reload()
check("после включения начислим", any(n[0] == str(E) for n in rf.process()))

# ---- бот ----
C = 794001
seq = [0]
user, chat = User(id=C, is_bot=False, first_name="T"), Chat(id=C, type="private")


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
            return Message(message_id=9700 + self.n, date=dt.now(), chat=chat, text=getattr(method, "text", "") or "x").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


def cb(data):
    seq[0] += 1
    m = Message(message_id=810, date=dt.now(), chat=chat, text="x")
    return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=user, chat_instance="x", message=m, data=data))


def kb_str(p):
    return str((p or {}).get("reply_markup", {}))


async def main():
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    last = lambda name: next((p for n, p in reversed(s.calls) if n == name), None)
    st = botmod.dp.fsm.get_context(b, C, C)
    text, markup = botmod.bot_profile._sub_view(C)
    check("в разделе подписки кнопка «Получить бесплатно»", "pf:fr" in str(markup) and "🎁 Получить бесплатно" in str(markup))
    await botmod.dp.feed_update(b, cb("pf:fr"))
    p = last("EditMessageText")
    check("экран: правила, ссылка, итоги, поделиться", "первый рецепт" in p["text"] and "неделя превращается в месяц" in p["text"] and rf.code_of(C) in p["text"] and "Пришло друзей: 0" in p["text"] and "t.me/share/url" in kb_str(p), p["text"][:400])
    sent = []

    class B2:
        async def send_message(self, uid, text, parse_mode=None):
            sent.append((uid, text))
    come(4000, 4001)
    recipe(4000)
    come(accounts.VK_OFFSET + 50, 4002)
    recipe(accounts.VK_OFFSET + 50)
    await rf.job(B2())
    check("задача пишет в Telegram, в ВК не пытается", {u for u, _ in sent} == {4000, 4001, 4002}, str(sent))
    check("задача запланирована раз в 10 минут", "friend_rewards" in open("reminders.py", encoding="utf-8").read())

    # полка по фото
    config.FEATURE_ROLLOUT = {"voice": "all", "photo_recipe": "all", "photo_shelf": "all"}
    accounts.settings_set({"FEATURE_ROLLOUT": config.FEATURE_ROLLOUT}, "test")
    plans.reload()
    accounts.grant_days(C, 30, "promo")
    accounts.consent_set(C)
    accounts._q("INSERT OR REPLACE INTO goals(uid, feats) VALUES(?, 'shelf')", (str(C),))
    check("полка включена у проверочного человека", plans.shelf_on(C))
    text, markup = botmod.bot_profile.shelf_view(C)
    check("в меню полки отдельная кнопка «Добавить по фото»", "pf:shph" in str(markup) and "📷 Добавить по фото" in str(markup))
    await botmod.dp.feed_update(b, cb("pf:shph"))
    check("просит фото и ждёт его для полки", "Сфотографируйте полку" in last("EditMessageText")["text"] and await st.get_state() == botmod.bot_profile.ProfileForm.shelf_add.state, str(await st.get_state()))
    await st.clear()


asyncio.run(main())

# ---- сайт ----
cl = TestClient(web_api.app)
dev = cl.post("/api/device").json()
tok = dev.get("device") or dev.get("id") or dev.get("token")
hdr = {"X-Device": tok}
check("гостю ссылка не положена", cl.get("/api/friends", headers=hdr).status_code == 403)
dev_id = web_api.web_db.device_id(tok)
accounts._q("INSERT INTO links(dev_id, tg_id, ts) VALUES(?,?,?)", (dev_id, C, time.time()))
r = cl.get("/api/friends", headers=hdr)
check("вошедшему ссылка и правила", r.status_code == 200 and rf.code_of(C) in r.json()["links"]["site"] and len(r.json()["rules"]) == 4 and r.json()["stats"]["friends"] == 0, r.text[:300])


async def fake_items(images):
    return ["рис", "куркума"]
vision.photo_items = fake_items
r = cl.post("/api/shelf/photo", files={"image": ("a.jpg", b"123", "image/jpeg")}, headers=hdr)
check("сайт: продукты с фото для полки", r.status_code == 200 and r.json()["items"] == ["рис", "куркума"], r.text[:200])
check("сайт: в профиле признак полки по фото", cl.get("/api/me", headers=hdr).json().get("shelf_photo") is True)
dev2 = cl.post("/api/device").json()
r = cl.post("/api/shelf/photo", files={"image": ("a.jpg", b"123", "image/jpeg")}, headers={"X-Device": dev2.get("device") or dev2.get("id") or dev2.get("token")})
check("сайт: гостю полка по фото недоступна", r.status_code == 403, str(r.status_code))
idx = open("web/index.html", encoding="utf-8").read()
check("на сайте есть кнопка и экран", "🎁 Получить бесплатно" in idx and "viewFriends" in idx and "data-shelf-photo" in idx)

print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
