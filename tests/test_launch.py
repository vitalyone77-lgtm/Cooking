"""Запуск рекламы: воронка (замки, пробная неделя), профиль с согласием (не использовать, цель КБЖУ, вес), пункт «по фото продуктов»."""
import asyncio
import os
import sys
import time
from datetime import datetime

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_ln.db", "accounts_ln.db", "accounts_ln.db-wal", "accounts_ln.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_ln.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_ln.db"
os.environ["WEB_DATA_DIR"] = "web_data_ln"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Update, CallbackQuery, Message, Chat, User

import config
config.ADMIN_PASSWORD, config.PARTNER_PASSWORD, config.WEB_SECRET = "owner-pass-1", "partner-pass-1", "secret-x"
config.LIMITS_FREE = "recipe=3/1,daymenu=1/7,week=0/3,missing=2/1,photo=1/1"
DEFAULT_PLANS = "month=30:290:220,year=365:2900:2200"
check_default = DEFAULT_PLANS in open("config.py", encoding="utf-8").read() and DEFAULT_PLANS in open(".env.example", encoding="utf-8").read()
config.PREMIUM_PLANS = DEFAULT_PLANS      # на сервере в .env могут стоять старые цены, а админка их всё равно перекрывает
import accounts
import plans
import profile_ctx
import usage
import keyboards as K
import bot as botmod
from states import ProfileForm

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


U = 880001

# ---------- цены ----------
offers = {o["key"]: o for o in plans.offers()}
check("цены по умолчанию записаны в config.py и .env.example", check_default)
check("цены по умолчанию: 290 ₽ в месяц, 2900 ₽ в год", offers["month"]["rub"] == 290 and offers["year"]["rub"] == 2900 and offers["year"]["days"] == 365, str(offers))

# ---------- данные профиля ----------
check("без согласия данных нет", accounts.consent_get(U) is False)
accounts.consent_set(U)
check("согласие записывается", accounts.consent_get(U) is True)
g = accounts.goals_set(U, feats=["never", "macro", "weight", "junk"], never="Кинза;  грибы\nпечень, нет", kcal="2000", protein=120, fat="70", carb=200, weight_goal="78,5")
check("«не использовать» чистится, лишние функции отбрасываются", g["never"] == "кинза, грибы, печень" and g["feats"] == ["never", "macro", "weight"], str(g))
check("цель текстом для поля КБЖУ", accounts.goal_text(U) == "2000 ккал, белки 120 г, жиры 70 г, углеводы 200 г", accounts.goal_text(U))
check("цель по весу хранится с десятыми", accounts.goals_get(U)["weight_goal"] == 78.5)
for bad in ({"kcal": 50}, {"kcal": "много"}, {"protein": 9999}, {"weight_goal": 5}):
    try:
        accounts.goals_set(U, **bad)
        ok = False
    except ValueError:
        ok = True
    check(f"недопустимое значение отклоняется: {bad}", ok)
accounts.weight_set(U, "84,2", "2026-10-01")
accounts.weight_set(U, 83.6, "2026-10-08")
accounts.weight_set(U, 83.1, "2026-10-08")
check("вес: записи по дням, повтор дня заменяет", accounts.weights_list(U) == [{"day": "2026-10-01", "kg": 84.2}, {"day": "2026-10-08", "kg": 83.1}], str(accounts.weights_list(U)))
try:
    accounts.weight_set(U, 83, "вчера")
    ok = False
except ValueError:
    ok = True
check("неверная дата отклоняется", ok)
check("разбор цели: полный текст", accounts.parse_goal("2000 ккал, белки 120, жиры 70, углеводы 200") == {"kcal": 2000, "protein": 120, "fat": 70, "carb": 200})
check("разбор цели: просто числа и просто ккал", accounts.parse_goal("1800 100 60 180")["carb"] == 180 and accounts.parse_goal("1800") == {"kcal": 1800})
check("разбор цели: «нет» очищает", accounts.parse_goal("нет") == {"kcal": None, "protein": None, "fat": None, "carb": None})
check("мини-график из символов", accounts.spark([1, 2, 3, 4]) == "▁▃▆█" or len(accounts.spark([1, 2, 3, 4])) == 4)

# ---------- «не использовать» в генерации: только в тарифе и с согласием ----------
config.PAYWALL_ENABLED = True
plans.reload()
profile_ctx.set_user(U)
check("без тарифа список не применяется", profile_ctx.current.get() is None)
accounts.grant_days(U, 30, "payment")
profile_ctx.set_user(U)
check("в тарифе список применяется", profile_ctx.current.get() == {"never": "кинза, грибы, печень"}, str(profile_ctx.current.get()))
profile_ctx.set_avoid("Орехи, кинза")
check("список профиля и «исключить» запроса объединяются без повторов", profile_ctx.current.get() == {"never": "кинза, грибы, печень, орехи"}, str(profile_ctx.current.get()))
check("проверка ответа ловит продукт из профиля", profile_ctx.hits("Добавьте свежую кинзу") == ["кинза"])
accounts.goals_set(U, feats=["macro", "weight"])
profile_ctx.set_user(U)
check("функция выключена — список не применяется", profile_ctx.current.get() is None)
accounts.goals_set(U, feats=["never", "macro", "weight"])
res = accounts.delete_my_data(U)
check("удаление данных: всё стёрто, подписка осталась", accounts.goals_get(U)["never"] == "" and accounts.weights_list(U) == [] and not accounts.consent_get(U)
      and accounts.is_premium(U), str(res))
profile_ctx.set_user(U)
check("после удаления список не применяется", profile_ctx.current.get() is None)

# ---------- воронка ----------
U2 = 880002
plans.reload()
check("тариф называется «Повар»", plans.PREMIUM_NAME == "Повар" and "Повар" in plans.PLAN_TITLES["premium"])
check("пробный доступен новому аккаунту", plans.trial_available(U2) is True)
check("гостю пробный не предлагается", plans.trial_available("w55") is False)
for _ in range(3):
    usage.try_spend(U2, "recipe", "bot")
sid, note = usage.try_spend(U2, "recipe", "bot")
check("на лимите сам включился тариф «Повар» с понятным сообщением", sid and "Лимиты на сегодня закончились" in note and "«Повар»" in note and "7 дней" in note, note)
check("после пробного он больше не предлагается", plans.trial_available(U2) is False)
check("в статусе указано имя тарифа", "«Повар»" in plans.status_text(U2))

# ---------- сайт ----------
from fastapi.testclient import TestClient
import web_api
with TestClient(web_api.app) as cl:
    d1 = cl.post("/api/device").json()["device"]
    h1 = {"X-Device": d1}
    me = cl.get("/api/me", headers=h1).json()
    check("гость: функции тарифа закрыты, пробный не предлагается", me["feat"]["pdf"] is False and me["feat"]["memory"] is False and me["trial"] is False and me["linked"] is False, str(me)[:300])
    check("гость: меню на день закрыто лимитом 0", me["left"]["daymenu"][1] == 0 and me["left"]["recipe"][1] >= 1, str(me["left"]))
    check("сайт знает цены и имя тарифа", me["name"] == "Повар" and {o["rub"] for o in me["offers"]} == {290, 2900} and me["trial_days"] == 7, str(me.get("offers")))
    check("гость не запускает пробный период", cl.post("/api/trial", headers=h1).status_code == 403)
    check("гостю профиль закрыт", cl.get("/api/profile", headers=h1).status_code == 403)
    check("нажатие на замок учитывается", cl.post("/api/gate", json={"feature": "pdf"}, headers=h1).status_code == 200
          and usage.events_today("gate_pdf", f"w{web_api.web_db.device_id(d1)}") == 1)

    # вход через Telegram
    TG = 880003
    dev_id = web_api.web_db.device_id(d1)
    code = accounts.new_login_code(TG)
    check("вход", cl.post("/api/link/claim", json={"code": code}, headers=h1).status_code == 200)
    me = cl.get("/api/me", headers=h1).json()
    check("вошедший: пробный предлагается, согласия ещё нет", me["trial"] is True and me["consent"] is False and me["feat"]["pdf"] is False, str(me)[:300])
    p = cl.get("/api/profile", headers=h1).json()
    check("профиль без согласия показывает только текст согласия", p["consent"] is False and "Диагнозы" in p["text"])
    check("без согласия ничего не сохранить", cl.put("/api/profile", json={"kcal": 2000}, headers=h1).status_code == 403
          and cl.post("/api/weight", json={"kg": 80}, headers=h1).status_code == 403)
    check("согласие", cl.post("/api/profile/consent", headers=h1).json()["consent"] is True)
    r = cl.put("/api/profile", json={"feats": ["macro", "weight", "never"], "kcal": 1900, "protein": 110, "fat": "", "carb": 190}, headers=h1)
    j = r.json()
    check("цель сохраняется, «не использовать» без тарифа отбрасывается", r.status_code == 200 and j["kcal"] == 1900 and j["fat"] is None and "never" not in j["feats"], r.text[:300])
    check("«не использовать» без тарифа — 402", cl.put("/api/profile", json={"never": "кинза"}, headers=h1).status_code == 402)
    check("плохие числа — 400", cl.put("/api/profile", json={"kcal": 20}, headers=h1).status_code == 400)
    cl.post("/api/weight", json={"kg": "82,5", "day": "2026-10-20"}, headers=h1)
    j = cl.post("/api/weight", json={"kg": 82.0, "day": "2026-10-27"}, headers=h1).json()
    check("вес сохраняется для графика", [w["kg"] for w in j["weights"]] == [82.5, 82.0], str(j["weights"]))
    me = cl.get("/api/me", headers=h1).json()
    check("цель подставится в анкету на сайте", me["goal_text"] == "1900 ккал, белки 110 г, углеводы 190 г", me.get("goal_text"))
    j = cl.delete("/api/weight", params={"day": "2026-10-20"}, headers=h1).json()
    check("запись веса удаляется", [w["day"] for w in j["weights"]] == ["2026-10-27"])
    r = cl.post("/api/trial", headers=h1)
    check("пробная неделя по кнопке на замке", r.status_code == 200 and r.json()["days"] == 7 and plans.plan_of(TG) == "premium", r.text)
    check("повторно пробный не даётся", cl.post("/api/trial", headers=h1).status_code == 409)
    me = cl.get("/api/me", headers=h1).json()
    check("в пробном все замки сняты", all(me["feat"].values()) and me["trial"] is False, str(me["feat"]))
    r = cl.put("/api/profile", json={"feats": ["macro", "weight", "never"], "never": "Кинза, грибы"}, headers=h1)
    check("в тарифе «не использовать» сохраняется", r.status_code == 200 and r.json()["never"] == "кинза, грибы" and "never" in r.json()["feats"], r.text[:200])
    me = cl.get("/api/me", headers=h1).json()
    check("сайт показывает, что исключается по профилю", me.get("never") == "кинза, грибы")
    check("удаление данных одним запросом", cl.delete("/api/profile/data", headers=h1).status_code == 200
          and cl.get("/api/profile", headers=h1).json()["consent"] is False)

    html = cl.get("/").text
    for needle, what in (("data-gate", "замки"), ("gateOpen", "окно «попробовать»"), ("data-buy", "галочки списка покупок"),
                         ("buyRow", "строки со списком"), ("weightChart", "график веса"), ("data-consent", "согласие")):
        check(f"страница сайта содержит: {what}", needle in html)

# ---------- бот ----------
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
            return Message(message_id=9100 + self.n, date=datetime.now(), chat=Chat(id=C, type="private"),
                           text=getattr(method, "text", "") or "").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


C = 880010
user = User(id=C, is_bot=False, first_name="T")
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


def last(s, name):
    return next((p for n, p in reversed(s.calls) if n == name), None)


def texts(s):
    return [p.get("text", "") for n, p in s.calls if n in ("SendMessage", "EditMessageText")]


async def main():
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    st = botmod.dp.fsm.get_context(b, C, C)

    # меню: пункт про фото продуктов — после аюрведы, только когда функция включена
    rows = [[x.text for x in r] for r in K.cuisine_kb(fridge=True).inline_keyboard]
    flat = [t for r in rows for t in r]
    check("меню: «По фото продуктов» стоит сразу после аюрведы", flat.index("📷 По фото продуктов") == flat.index("🍛 Аюрведа") + 1, str(rows))
    check("меню без функции — как раньше, без этого пункта", all("фото" not in x.text for r in K.cuisine_kb().inline_keyboard for x in r))
    await botmod.dp.feed_update(b, cb("fridge:start"))
    p = last(s, "AnswerCallbackQuery")
    check("функция выключена — понятный отказ", p and "недоступна" in (p.get("text") or ""), str(p))
    accounts.settings_set({"FEATURE_ROLLOUT": {"photo_recipe": "all", "photo_shelf": "off", "voice": "off"}}, "test")
    plans.reload()
    await botmod.dp.feed_update(b, cb("fridge:start"))
    check("инструкция: продукты должны попасть в кадр", any("попали в кадр" in t for t in texts(s)), str(texts(s)[-1:]))
    await st.clear()
    await botmod.show_main_menu(Message(message_id=1, date=datetime.now(), chat=chat, text="x").as_(b), st)
    m = last(s, "SendMessage")
    check("главное меню показывает пункт, когда функция включена", "📷 По фото продуктов" in btns(m), str(btns(m)))

    # профиль: согласие
    config.PAYWALL_ENABLED = False
    plans.reload()
    await botmod.dp.feed_update(b, cb("profile:open", 800))
    m = last(s, "SendMessage")
    check("профиль: сначала предупреждение и кнопка «Согласен»", "Диагнозы" in m["text"] and "✅ Согласен" in btns(m) and "🎯 Цель КБЖУ" not in btns(m), str(btns(m)))
    await botmod.dp.feed_update(b, cb("pf:goal", 800))
    check("без согласия цель не вводится", last(s, "AnswerCallbackQuery").get("show_alert") is True)
    await botmod.dp.feed_update(b, cb("pf:consent", 800))
    m = last(s, "EditMessageText")
    check("после согласия появились функции и удаление данных", {"🎯 Цели и вес", "🧩 Функции", "🗑 Удалить"} <= set(btns(m)), str(btns(m)))

    # цель
    await botmod.dp.feed_update(b, cb("pf:goal", 800))
    check("цель: ждём текст", await st.get_state() == ProfileForm.goal.state)
    await botmod.dp.feed_update(b, msg("пять тысяч"))
    check("цель: непонятный текст — подсказка, ждём дальше", await st.get_state() == ProfileForm.goal.state and "Не нашёл чисел" in texts(s)[-1], texts(s)[-1])
    await botmod.dp.feed_update(b, msg("2000 ккал, белки 120, жиры 70, углеводы 200"))
    check("цель сохранена и видна в профиле", accounts.goal_text(C) == "2000 ккал, белки 120 г, жиры 70 г, углеводы 200 г" and "macro" in accounts.goals_get(C)["feats"]
          and any("2000 ккал" in t for t in texts(s)[-2:]), str(texts(s)[-2:]))
    value, label = botmod._last_value(C, "macro_goal")
    check("в анкете «Цель по КБЖУ» предлагается цель из профиля", label == "Моя цель" and value.startswith("2000 ккал"), f"{label} {value}")
    markup = botmod._skip("macro_goal", C)
    check("кнопка называется «Моя цель»", any("Моя цель" in x.text for r in markup.inline_keyboard for x in r))

    # вес
    await botmod.dp.feed_update(b, cb("pf:weight", 800))
    await botmod.dp.feed_update(b, msg("82,5 кг"))
    await botmod.dp.feed_update(b, cb("pf:weight", 800))
    await botmod.dp.feed_update(b, msg("цель 78"))
    g = accounts.goals_get(C)
    check("вес записан, цель по весу сохранена", accounts.weights_list(C)[-1]["kg"] == 82.5 and g["weight_goal"] == 78 and "weight" in g["feats"], str(g))
    import bot_profile as _bp
    check("в «Целях и весе» виден вес", "Вес: 82.5 кг, цель 78" in _bp._goals_view(C)[0], _bp._goals_view(C)[0])

    # «не использовать» — только в тарифе
    config.PAYWALL_ENABLED = True
    plans.reload()
    await botmod.dp.feed_update(b, cb("pf:never", 800))
    check("без тарифа «не использовать» закрыто", last(s, "AnswerCallbackQuery").get("show_alert") is True and await st.get_state() is None)
    accounts.grant_days(C, 30, "payment")
    config.PAYWALL_ENABLED = True
    plans.reload()
    await botmod.dp.feed_update(b, cb("pf:never", 800))
    check("в тарифе — ждём список", await st.get_state() == ProfileForm.never.state)
    await botmod.dp.feed_update(b, msg("кинза, грибы"))
    check("список сохранён", accounts.goals_get(C)["never"] == "кинза, грибы" and "never" in accounts.goals_get(C)["feats"])
    check("на экране «Проверим запрос» видно, что исключено по профилю", "кинза, грибы" in botmod._left_line(C, "recipe"), botmod._left_line(C, "recipe"))

    # удаление данных
    await botmod.dp.feed_update(b, cb("pf:del", 800))
    m = last(s, "EditMessageText")
    check("удаление: сначала подтверждение", "🗑 Да, удалить" in btns(m))
    await botmod.dp.feed_update(b, cb("pf:delyes", 800))
    check("данные удалены, согласие снято", not accounts.consent_get(C) and accounts.weights_list(C) == [] and accounts.goals_get(C)["never"] == "")

    # пробная неделя из бота
    config.PAYWALL_ENABLED = True
    D = 880011
    kbm = botmod.bot_profile.upsell_kb(D)
    check("при отказе предлагается пробная неделя", kbm and any("Попробовать 7 дней" in x.text for r in kbm.inline_keyboard for x in r), str(kbm))
    chat2 = Chat(id=D, type="private")
    seq[0] += 1
    m2 = Message(message_id=601, date=datetime.now(), chat=chat2, text="x")
    await botmod.dp.feed_update(b, Update(update_id=seq[0], callback_query=CallbackQuery(
        id=str(seq[0]), from_user=User(id=D, is_bot=False, first_name="D"), chat_instance="x", message=m2, data="pf:trial")))
    check("кнопка подключила тариф на 7 дней", plans.plan_of(D) == "premium" and any("«Повар»" in t for t in texts(s)[-3:]), str(texts(s)[-3:]))

asyncio.run(main())
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
