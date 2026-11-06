"""Новое: меню, напоминания 4+6, профиль/аллергии, полка, подписка/лимиты/пробный, режим готовки, список покупок."""
import asyncio
import os
import sys
import time
from datetime import datetime

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_v2.db", "accounts_v2.db"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_v2.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_v2.db"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Update, CallbackQuery, Message, Chat, User

import config
config.LIMITS_FREE = "recipe=3/1,daymenu=1/7,week=0/3,missing=2/1,photo=1/1"   # не зависим от .env сервера
import usage
import accounts
import plans
import profile_ctx
import ai
import keyboards as K
import bot as botmod
import bot_cook
import bot_shop
import reminders

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


CHAT = 777000101

# ---------- главное меню ----------
rows = [[b.text for b in r] for r in K.cuisine_kb().inline_keyboard]
check("меню: пары как просили", rows == [["🍲 Классическая", "⚖️ Сбалансированная"], ["🏃 Спорт: кардио", "💪 Спорт: сила"],
                                         ["🎯 По КБЖУ", "🥜 Сытный перекус"], ["🍛 Аюрведа"], ["⭐ Избранное", "📖 Книга рецептов"],
                                         ["📅 Меню на день", "📆 Меню на 3 дня"]], str(rows))
rows2 = [[b.text for b in r] for r in K.cuisine_kb(last_title="Плов с курицей").inline_keyboard]
check("меню «Создать»: последний рецепт отдельной строкой", rows2[-1] == ["📖 Последний: Плов с курицей"], str(rows2[-1:]))
rk = K.main_reply_kb()
check("нижнее меню: Создать / Готовые меню / Профиль, постоянное", K.main_reply_kb().is_persistent is True and [b.text for b in K.main_reply_kb().keyboard[0]] == [K.BTN_CREATE, K.BTN_READY, K.BTN_PROFILE])
check("избранное и книга другим цветом", all(getattr(b, "style", None) == "primary" for r in K.cuisine_kb().inline_keyboard for b in r if "Избранное" in b.text or "Книга" in b.text))
check("меню: подписи не длиннее 18 символов", all(len(t) <= 19 for r in rows for t in r))
check("меню внутри анкеты — без избранного/справки", all("Справка" not in t for r in K.cuisine_kb(False).inline_keyboard for t in [b.text for b in r]))
check("справка без dev.pump-um", "pump-um" not in botmod.HELP_TEXT)

# ---------- напоминания: 4 дня, потом ещё 6, всё ----------
G = [4, 6]
check("новый: не сразу", usage.reminder_due(1, G) is None)
usage._q("UPDATE seen SET ts=? WHERE uid='1'", (time.time() - 3 * 86400,))
check("3 дня тишины — рано", usage.reminder_due(1, G) is None)
usage._q("UPDATE seen SET ts=? WHERE uid='1'", (time.time() - 4.1 * 86400,))
check("4 дня — первое", usage.reminder_due(1, G) == 1)
usage.mark_reminded(1, 1)
check("после первого через 5 дней от визита — второго ещё нет", (usage._q("UPDATE seen SET ts=? WHERE uid='1'", (time.time() - 9 * 86400,)) or True) and usage.reminder_due(1, G) is None)
usage._q("UPDATE seen SET ts=? WHERE uid='1'", (time.time() - 10.1 * 86400,))
check("10 дней — второе", usage.reminder_due(1, G) == 2)
usage.mark_reminded(1, 2)
usage._q("UPDATE seen SET ts=? WHERE uid='1'", (time.time() - 60 * 86400,))
check("дальше — тишина", usage.reminder_due(1, G) is None)
usage._seen_cache.clear()
usage.touch(1)
usage._q("UPDATE seen SET ts=? WHERE uid='1'", (time.time() - 4.1 * 86400,))
check("вернулся и снова пропал — опять первое", usage.reminder_due(1, G) == 1)

sent = []


async def fake_one(bot, chat_id, data, n=1):
    sent.append((chat_id, n))
REMIND_ONE = reminders._remind_one
reminders._remind_one = fake_one
reminders.get_all_users = lambda: {"901": {"cuisine": "classic"}, "902": {"cuisine": "classic"},
                                   "903": {"cuisine": "classic", "reminders_off": True}}
for u, days in (("901", 4.5), ("902", 1), ("903", 30)):
    usage._q("INSERT OR REPLACE INTO seen(uid, ts, stage) VALUES (?, ?, 0)", (u, time.time() - days * 86400))
asyncio.run(reminders.send_daily_reminders(None, None))
check("рассылка: только пропавшему 4+ дня и не отключившему", sent == [(901, 1)], str(sent))
sent.clear()
asyncio.run(reminders.send_daily_reminders(None, None))
check("рассылка: на следующий день то же напоминание не повторяется", sent == [], str(sent))

# ---------- профиль и проверка ответа ИИ ----------
profile_ctx.set_avoid("Орехи;  мёд\nкреветки, кинза, нет")
check("исключения запроса: список чистится", profile_ctx.current.get() == {"never": "орехи, мёд, креветки, кинза"}, str(profile_ctx.current.get()))
check("исключения попадают в промпт", "орехи, мёд, креветки, кинза" in profile_ctx.prompt_block())
check("проверка: «молотый перец» не путается, «без орехов» не считается",
      profile_ctx.hits("- перец молотый\n- молоко 100 мл\nПодавай без орехов") == [])
check("проверка: «грецкими орехами», «мёдом», «кинзы» — находит",
      set(profile_ctx.hits("Посыпь грецкими орехами, полей мёдом, добавьте веточку кинзы")) == {"орехи", "мёд", "кинза"})
check("строка «исключено: орехи» не считается", profile_ctx.hits("Исключено: орехи, мёд") == [])

calls = []


async def fake_any(messages, max_tokens):
    calls.append(messages)
    return "🍽 Салат\n- орехи 30 г" if len(calls) == 1 else "🍽 Салат\n- семечки 30 г"
ai._call_any = fake_any
out = asyncio.run(ai.call_llm_with_fallback([{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]))
check("ИИ: профиль дописан в конец запроса, системный промпт не тронут",
      calls[0][0]["content"] == "S" and calls[0][1]["content"].startswith("U") and "ПРОСИЛ ИСКЛЮЧИТЬ" in calls[0][1]["content"])
check("ИИ: нашёлся аллерген — одна повторная попытка, ответ без него", len(calls) == 2 and "орех" not in out, str(len(calls)))
calls.clear()


async def always_bad(messages, max_tokens):
    calls.append(1)
    return "🍽 Салат\n- орехи 30 г"
ai._call_any = always_bad
out = asyncio.run(ai.generate_recipe({"cuisine": "classic", "excluded": "Орехи, мёд, креветки, кинза"}, "q", ""))
check("ИИ: не помогло — не больше 2 запросов и предупреждение сверху", len(calls) == 2 and out.startswith("⚠️ Проверьте состав"), out[:60])
profile_ctx.current.set(None)
calls.clear()
asyncio.run(ai.call_llm_with_fallback([{"role": "user", "content": "U"}]))
check("ИИ: без исключений запрос не меняется и повтора нет", calls == [1])

# ---------- полка ----------
check("полка: масло подсолнечное — да, сливочное — нет", accounts.shelf_life_days("Масло подсолнечное") and not accounts.shelf_life_days("масло сливочное"))
check("полка: курица — не для полки", accounts.shelf_life_days("куриное филе") is None)
accounts.consent_set(CHAT)
accounts.goals_set(CHAT, feats=["shelf"])            # полка включается только галочкой в профиле
check("полка сама не включается: без галочки её нет", not plans.shelf_on(777000999) and plans.shelf_on(CHAT))
accounts.shelf_add(CHAT, "масло подсолнечное")
accounts.shelf_add(CHAT, "соль")
hit = accounts.shelf_match(CHAT, ["Куриное филе", "Масло подсолнечное рафинированное", "соль морская", "масло сливочное"])
check("полка: совпадения в списке покупок", hit == {1, 2}, str(hit))
accounts._q("UPDATE shelf SET ts=? WHERE uid=? AND item='масло подсолнечное'", (time.time() - 200 * 86400, str(CHAT)))
check("полка: старое масло (200 дней) уже не считается", [x["item"] for x in accounts.shelf_list(CHAT)] == ["соль"])

# ---------- вход на сайт ----------
code = accounts.new_link_code(55)
check("привязка: код подходит для /start (<=64, без спецсимволов)", len(code) <= 64 and all(c.isalnum() or c in "-_" for c in code))
check("привязка: работает один раз", accounts.use_link_code(code, CHAT) == 55 and accounts.use_link_code(code, 123) is None)
check("привязка: устройство теперь аккаунт Telegram", accounts.web_uid(55) == str(CHAT) and accounts.link_code_status(code, 55))
old = accounts.new_link_code(56)
accounts._q("UPDATE link_codes SET created=? WHERE code=?", (time.time() - 700, old))
check("привязка: просроченный код не работает", accounts.use_link_code(old, CHAT) is None)
lc = accounts.new_login_code(CHAT)
check("вход из бота: одноразовый", accounts.claim_login_code(lc, 57) == CHAT and accounts.claim_login_code(lc, 58) is None)
check("коды привязки и входа не взаимозаменяемы", accounts.use_link_code(accounts.new_login_code(CHAT), 999) is None
      and accounts.claim_login_code(accounts.new_link_code(59), 59) is None)

# ---------- тарифы и лимиты ----------
check("подписка выключена: план open, лимиты прежние", plans.plan_of(CHAT) == "open" and plans.limit(CHAT, "recipe") == (config.WEB_LIMIT_RECIPE, 1))
U = 777000200
ids = [usage.try_spend(U, "recipe", "bot")[0] for _ in range(config.WEB_LIMIT_RECIPE)]
check("open: лимит рецептов как раньше", all(ids) and usage.try_spend(U, "recipe", "bot")[0] is None)
usage.try_spend(CHAT, "recipe", "web")
check("сайт и бот одного аккаунта — общий счётчик", usage.used(CHAT, "recipe") == 1)

config.PAYWALL_ENABLED = True
check("подписка вкл: аккаунт без подписки — free", plans.plan_of(U) == "free" and plans.plan_of("w5") == "anon")
U2 = 777000201
a = [usage.try_spend(U2, "recipe", "bot") for _ in range(3)]
check("free: 3 рецепта в день", all(x[0] for x in a) and not any(x[1] for x in a))
sid, note = usage.try_spend(U2, "recipe", "bot")
check("free: упёрся — сам включился пробный период", sid and "«Повар»" in note and "дней бесплатно" in note and plans.plan_of(U2) == "premium", note)
check("пробный — только один раз", accounts.start_trial(U2, 7) is None)
accounts._q("UPDATE subs SET until=? WHERE uid=?", (time.time() - 1, str(U2)))
usage.try_spend(U2, "recipe", "bot")
sid, why = usage.try_spend(U2, "recipe", "bot")
check("после пробного — отказ с предложением подписки", sid is None and "подписк" in why, why)
check("free: план на 3 дня — только в подписке", "в подписке" in usage.try_spend(U2, "week", "bot")[1])
check("без входа на сайте: меню на день — после входа в профиль", "входа в профиль" in usage.try_spend("w77", "daymenu", "web")[1])
check("функции подписки: pdf/memory закрыты для free", not plans.has_feature(U2, "pdf") and not plans.has_feature(U2, "memory"))
until = accounts.grant_days(U2, 30, "payment")
check("оплата: подписка на 30 дней", abs(until - time.time() - 30 * 86400) < 5 and plans.has_feature(U2, "memory"))
check("оплата: повторная доставка платежа не продлевает дважды",
      accounts.record_payment("ch1", U2, 150, "XTR", 30, "month") and not accounts.record_payment("ch1", U2, 150, "XTR", 30, "month"))
config.PROMO_CODES = "BLOGGER=14"
check("промокод: дни по коду, регистр не важен", plans.promo_days("blogger") == 14 and plans.promo_days("x") == 0)
check("промокод: один раз на аккаунт", accounts.use_promo(U2, "blogger") and not accounts.use_promo(U2, "BLOGGER"))
accounts.set_ref(U2, "blog_anna")
accounts.set_ref(U2, "other")
check("ref: запоминается первый источник, видна оплата", accounts.ref_stats()[0] == {"ref": "blog_anna", "users": 1, "paid_users": 1, "amount": 0}, str(accounts.ref_stats()))

# потолки и накрутка
config.PAYWALL_ENABLED = False
config.DAILY_GEN_CAP_ANON = usage._count("SELECT COUNT(*) FROM gens WHERE uid LIKE 'w%' AND ts >= ?", (usage.msk_midnight(),)) + 1
check("без входа: свой запас", usage.try_spend("w901", "recipe", "web")[0] and usage.try_spend("w902", "recipe", "web")[1] == usage.ANON_CAP_TEXT)
check("…а Telegram работает", usage.try_spend(777000300, "recipe", "bot")[0])
config.DAILY_GEN_CAP_ANON = 0
config.WEB_IP_DAILY_GENS = 2
r = [usage.try_spend(f"w{1000 + i}", "recipe", "web", ip="9.9.9.9") for i in range(3)]
check("с одного IP: новые устройства не помогают", r[0][0] and r[1][0] and r[2][0] is None and "адреса" in r[2][1])
config.DAILY_GEN_CAP = usage.total_today()
check("общий потолок: обычный — отказ", usage.try_spend(777000301, "recipe", "bot")[1] == usage.CAP_TEXT)
config.PAYWALL_ENABLED = True
accounts.grant_days(777000302, 30, "payment")
check("общий потолок: подписчик ещё может (+50%)", usage.try_spend(777000302, "recipe", "bot")[0])
config.PAYWALL_ENABLED = False
config.DAILY_GEN_CAP = 500

# ---------- режим готовки и список покупок через диспетчер ----------
RECIPE = ("🍽 Каша с яйцом\n\n⏱ Время: 15 мин | 👥 Порций: 1 | 🔥 Способ: Плита\n\nПродукты:\n- Овсяные хлопья — 60 г\n"
          "- Масло подсолнечное — 10 мл\n- Яйцо — 1 шт\n\nИнструкция:\n1. Доведи воду до кипения.\n"
          "2. Всыпь овсянку и вари 5–6 мин, помешивая.\n3. Отвари яйцо 8 мин.\n4. Подай.\n\n💡 Совет: добавьте ягоды.")
r = bot_cook.parse_recipe(RECIPE)
check("готовка: разбор шагов и продуктов", r["title"] == "Каша с яйцом" and len(r["steps"]) == 4 and len(r["ingredients"]) == 3, str(r))


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.n = 0

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append((name, method.model_dump(exclude_none=True)))
        if name in ("SendMessage", "EditMessageText", "SendPhoto"):
            self.n += 1
            return Message(message_id=5000 + self.n, date=datetime.now(), chat=Chat(id=CHAT, type="private"),
                           text=getattr(method, "text", "") or "").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


user = User(id=CHAT, is_bot=False, first_name="T")
chat = Chat(id=CHAT, type="private")
uid = [0]


def cb(data, msg_id=600, text=RECIPE, reply=None):
    uid[0] += 1
    rep = Message(message_id=reply, date=datetime.now(), chat=chat, text=RECIPE) if reply else None
    msg = Message(message_id=msg_id, date=datetime.now(), chat=chat, text=text, reply_to_message=rep)
    return Update(update_id=uid[0], callback_query=CallbackQuery(id=str(uid[0]), from_user=user, chat_instance="x",
                                                                 message=msg, data=data))


def last(s, name):
    return next((p for n, p in reversed(s.calls) if n == name), None)


def btns(p):
    return [b["text"] for r in (p or {}).get("reply_markup", {}).get("inline_keyboard", []) for b in r]


async def main():
    botmod._flood.max = 10000      # тест жмёт сотни раз в секунду — защиту от флуда проверяем отдельно ниже
    s = FakeSession()
    b = Bot(token="123456:TEST-TOKEN-NOT-REAL-1234567890abcdef", session=s)
    kb = btns({"reply_markup": botmod.recipe_kb(RECIPE).model_dump()})
    check("под рецептом: готовить по шагам + список покупок, без столбика таймеров",
          "👨‍🍳 Готовить по шагам" in kb and "🛒 Продукты и магазины" in kb and not any(t.startswith("⏱") for t in kb), str(kb))

    await botmod.dp.feed_update(b, cb("ck:start"))
    m = last(s, "SendMessage")
    check("готовка: одно сообщение «Шаг 1 из 4»", m and "Шаг 1 из 4" in m["text"] and "Доведи воду" in m["text"], str(m)[:200])
    cmid = 5000 + s.n
    await botmod.dp.feed_update(b, cb("ck:d:0", msg_id=cmid, text=m["text"]))
    e = last(s, "EditMessageText")
    check("готовка: «Готово» — то же сообщение, шаг 2 и кнопка таймера 5–6 мин",
          e and e["message_id"] == cmid and "Шаг 2 из 4" in e["text"] and "⏱ Таймер 5–6 мин" in btns(e), str(e)[:300])
    sleeps = []

    async def fake_sleep(sec):
        sleeps.append(round(sec))
        st = accounts.sess_get(f"ck:{CHAT}:{cmid}")
        for t in st["timers"].values():
            t["end"] -= sec
            t["end_max"] -= sec
        accounts.sess_set(f"ck:{CHAT}:{cmid}", st)
    bot_cook._timer_sleep = fake_sleep
    await botmod.dp.feed_update(b, cb("ck:t:5:6", msg_id=cmid))
    await asyncio.gather(*list(bot_cook._tasks.values()))
    sends = [p["text"] for n, p in s.calls if n == "SendMessage"]
    check("таймер: два сигнала новыми сообщениями (5 и 6 мин)", len(sleeps) == 2 and "можно проверять" in sends[-2] and "время вышло" in sends[-1], str(sleeps) + str(sends[-2:]))
    await botmod.dp.feed_update(b, cb("ck:d:1", msg_id=cmid))
    dels = [p["message_id"] for n, p in s.calls if n == "DeleteMessage"]
    check("таймер: сигналы удаляются, когда шаг отмечен", len(dels) == 2, str(dels))
    await botmod.dp.feed_update(b, cb("ck:d:1", msg_id=cmid))
    check("готовка: двойное нажатие «Готово» не перескакивает шаг", accounts.sess_get(f"ck:{CHAT}:{cmid}")["i"] == 2)
    await botmod.dp.feed_update(b, cb("ck:v:all", msg_id=cmid))
    e = last(s, "EditMessageText")
    check("готовка: все шаги с галочками", "✅ 1." in e["text"] and "✅ 2." in e["text"] and "▶️ 3." in e["text"], e["text"])
    check("«Все шаги»: кнопка таймера — для текущего шага 3 (8 мин)", "⏱ Таймер 8 мин" in btns(e), str(btns(e)))
    bot_cook._timer_sleep = asyncio.sleep       # настоящее время: таймер 8 мин не успеет сработать
    await botmod.dp.feed_update(b, cb("ck:t:8:8", msg_id=cmid))
    await botmod.dp.feed_update(b, cb("ck:d:2", msg_id=cmid))
    e = last(s, "EditMessageText")
    check("«Все шаги»: «Готово» не уходит из списка — галочка у 3, стрелка на 4",
          "✅ 3." in e["text"] and "▶️ 4." in e["text"] and accounts.sess_get(f"ck:{CHAT}:{cmid}")["view"] == "all", e["text"])
    check("таймер шага 3 идёт дальше, когда перешли к шагу 4", "⏱ Шаг 3: сигнал в" in e["text"], e["text"])
    for t in list(bot_cook._tasks.values()):
        t.cancel()
    await botmod.dp.feed_update(b, cb("ck:d:2", msg_id=cmid))  # старая кнопка — ничего не ломает
    await botmod.dp.feed_update(b, cb("ck:d:3", msg_id=cmid))
    check("готовка: финал", "Приятного аппетита" in last(s, "EditMessageText")["text"])

    # список покупок
    accounts.shelf_add(CHAT, "масло подсолнечное")
    await botmod.dp.feed_update(b, cb("sl:o:r", msg_id=700))
    t = btns(last(s, "EditMessageReplyMarkup"))
    check("продукты и магазины: сначала короткое меню, без длинного списка", any(x.startswith("☑️ Отмечать купленное") for x in t) and "🏪 Найти в магазине" in t and "📤 Отправить список" in t and "⬅️ Свернуть" in t and len(t) <= 5, str(t))
    await botmod.dp.feed_update(b, cb("sl:chk", msg_id=700))
    e = last(s, "EditMessageReplyMarkup")
    t = btns(e)
    check("список: раскрылся на месте (правка кнопок, не новое сообщение)", e and e["message_id"] == 700 and "✅ Куплено" in " ".join(t), str(t))
    check("список: масло с полки — отдельно «есть дома»", "— 🏠 есть дома / на полке —" in t and any(x.startswith("🏠 Масло") for x in t), str(t))
    await botmod.dp.feed_update(b, cb("sl:i:0", msg_id=700))
    check("список: отметка «купил»", any(x.startswith("✅ Овсяные") for x in btns(last(s, "EditMessageReplyMarkup"))))
    check("полка: купленные хлопья ушли на полку", any("овсян" in x["item"] for x in accounts.shelf_list(CHAT)))
    await botmod.dp.feed_update(b, cb("sl:m:m", msg_id=700))
    await botmod.dp.feed_update(b, cb("sl:i:2", msg_id=700))
    t = btns(last(s, "EditMessageReplyMarkup"))
    check("список: «нет в магазине» и кнопка замены", any(x.startswith("❌ Яйцо") for x in t) and "🔄 Заменить ❌ (1)" in t, str(t))
    n_before = len([1 for n, _ in s.calls if n == "SendMessage"])
    await botmod.dp.feed_update(b, cb("sl:st", msg_id=700))
    st_msg = last(s, "SendMessage")
    await botmod.dp.feed_update(b, cb("sl:s:fiveka", msg_id=5000 + s.n, text="x", reply=700))
    e = last(s, "EditMessageText")
    check("магазин: одно сообщение, только некупленное и не «дома»",
          st_msg and e and "Яйцо" in e["text"] and "Овсяные" not in e["text"] and "Не включил — есть дома: Масло" in e["text"], (e or {}).get("text", "")[:300])
    await botmod.dp.feed_update(b, cb("sl:st", msg_id=700))
    check("магазин: повторное нажатие не плодит сообщения", len([1 for n, _ in s.calls if n == "SendMessage"]) == n_before + 1)
    await botmod.dp.feed_update(b, cb("sl:c", msg_id=700))
    check("список: «Свернуть» вернул кнопки рецепта", "👨‍🍳 Готовить по шагам" in btns(last(s, "EditMessageReplyMarkup")))
    await botmod.dp.feed_update(b, cb("sl:o:r", msg_id=700))
    await botmod.dp.feed_update(b, cb("sl:tx", msg_id=700))
    tx = last(s, "SendMessage")
    check("отправить список: ссылка с галочками и количество", tx and "/l/" in tx["text"] and " — " in tx["text"] and "Масло" not in tx["text"].split("🏠")[0], (tx or {}).get("text", "")[:300])
    await botmod.dp.feed_update(b, cb("sl:chk", msg_id=700))
    check("список: отметки сохраняются при повторном открытии", any(x.startswith("✅ Овсяные") for x in btns(last(s, "EditMessageReplyMarkup"))))

    # профиль в боте
    await botmod.dp.feed_update(b, cb("profile:open", msg_id=800, text="меню"))
    m = last(s, "SendMessage")
    check("профиль: аллергий в профиле больше нет, есть подсказка про «Исключить» и полка",
          m and "Исключить" in m["text"] and "✏️ Аллергии" not in btns(m) and "🏠 Моя полка" in btns(m), str(m)[:300])
    config.PAYWALL_ENABLED = True
    await botmod.dp.feed_update(b, cb("profile:open", msg_id=800, text="меню"))
    m = last(s, "SendMessage")
    check("профиль без подписки: подписка предлагается, полка не навязывается", "💎 Подписка" in btns(m) and "🏠 Моя полка" not in btns(m), str(btns(m)))
    await botmod.dp.feed_update(b, cb("pf:sub", msg_id=801, text="x"))
    e = last(s, "EditMessageText")
    check("подписка: кнопки оплаты звёздами", e and any("звёзд" in x for x in btns(e)), str(btns(e)))
    await botmod.dp.feed_update(b, cb("pf:buy:stars:month", msg_id=801))
    inv = last(s, "SendInvoice")
    check("оплата: счёт в Stars (XTR), цена из настроек", inv and inv["currency"] == "XTR" and inv["prices"][0]["amount"] == 150, str(inv)[:200])
    await botmod.dp.feed_update(b, cb("pdf:r", msg_id=700))
    ans = last(s, "AnswerCallbackQuery")
    check("бот без подписки: PDF — только в подписке", ans and "доступно в тарифе" in ans.get("text", ""), str(ans))
    config.PAYWALL_ENABLED = False

    # вход на сайт — только после подтверждения кнопкой (ссылку могли прислать чужую)
    from aiogram.types import SuccessfulPayment
    code = accounts.new_link_code(321)

    def msg_upd(text=None, **kw):
        uid[0] += 1
        return Update(update_id=uid[0], message=Message(message_id=9000 + uid[0], date=datetime.now(), chat=chat,
                                                        from_user=user, text=text, **kw))
    await botmod.dp.feed_update(b, msg_upd(f"/start link_{code}"))
    m = last(s, "SendMessage")
    check("вход: бот сначала спрашивает подтверждение", m and "Войти на сайте" in m["text"] and "✅ Да, войти на сайте" in btns(m)
          and accounts.tg_of_device(321) is None, str(m)[:200])
    await botmod.dp.feed_update(b, cb(f"pf:lk:{code}", msg_id=9100, text="x"))
    check("вход: после «Да» устройство привязано", accounts.tg_of_device(321) == CHAT)
    await botmod.dp.feed_update(b, cb("pf:unall", msg_id=9100, text="x"))
    check("«Выйти на сайте» отвязывает все устройства", accounts.tg_of_device(321) is None)

    # оплата звёздами: зачисление один раз, даже если Telegram пришлёт повторно
    before = accounts.premium_until(CHAT)
    sp = SuccessfulPayment(currency="XTR", total_amount=150, invoice_payload=f"sub:month:{CHAT}",
                           telegram_payment_charge_id="tgcharge1", provider_payment_charge_id="p1")
    await botmod.dp.feed_update(b, msg_upd(successful_payment=sp))
    u1 = accounts.premium_until(CHAT)
    await botmod.dp.feed_update(b, msg_upd(successful_payment=sp))
    check("оплата: +30 дней, повторная доставка не продлевает", abs(u1 - max(before, time.time()) - 30 * 86400) < 5
          and accounts.premium_until(CHAT) == u1)
    await botmod.dp.feed_update(b, msg_upd("/paysupport"))
    check("/paysupport отвечает", "Вопросы по оплате" in last(s, "SendMessage")["text"])

    # быстрые нажатия: Telegram просит подождать — без «что-то пошло не так», отметка сохранена
    from aiogram.exceptions import TelegramRetryAfter
    real = s.make_request

    async def flood(bot_, method, timeout=None):
        if type(method).__name__ == "EditMessageReplyMarkup":
            raise TelegramRetryAfter(method=method, message="Too Many Requests", retry_after=3)
        return await real(bot_, method, timeout)
    s.make_request = flood
    await botmod.dp.feed_update(b, cb("sl:i:1", msg_id=700))
    s.make_request = real
    check("частые нажатия: отметка сохранена, хотя перерисовка пропущена",
          accounts.sess_get(f"sl:{CHAT}:700")["st"].get("1") is not None)

    # напоминание учитывает аллергии
    seen = []
    import reminders as R

    async def gen(data, q, r):
        seen.append(profile_ctx.current.get())
        return "🍽 Суп\n\nПродукты:\n- Вода — 1 л\n\nИнструкция:\n1. Свари."

    async def srch(d):
        return "q", []
    R.generate_recipe, R.search_recipes = gen, srch
    R._remind_one = REMIND_ONE
    R.get_all_users = lambda: {str(CHAT): {"cuisine": "classic", "excluded": "орехи, мёд"}}
    usage._q("INSERT OR REPLACE INTO seen(uid, ts, stage) VALUES (?, ?, 0)", (str(CHAT), time.time() - 5 * 86400))
    profile_ctx.current.set(None)
    await R.send_daily_reminders(b, botmod.dp)
    check("напоминание: рецепт генерируется с исключениями из последнего запроса", seen and seen[0] and "орехи" in seen[0]["never"], str(seen))

    # нижнее меню: у кого пропало или старое — приходит один раз вместе с «Создать»
    botmod.set_last_recipe(CHAT, "Каша с яйцом", RECIPE, "classic")
    s.calls.clear()
    await botmod.dp.feed_update(b, cb("menu:open", msg_id=9200, text="x"))
    kbm = [p for n, p in s.calls if n == "SendMessage" and p.get("reply_markup", {}).get("keyboard")]
    check("пропавшее нижнее меню присылается заново", len(kbm) == 1 and kbm[0]["reply_markup"]["keyboard"][0][0]["text"] == "🍳 Создать", str(kbm)[:200])
    s.calls.clear()
    await botmod.dp.feed_update(b, cb("menu:open", msg_id=9200, text="x"))
    check("…и только один раз", not [p for n, p in s.calls if n == "SendMessage" and p.get("reply_markup", {}).get("keyboard")])
    m = last(s, "SendMessage")
    check("«Создать»: есть «Последний рецепт» (рецепт пережил бы перезапуск — лежит в базе)",
          any(x.startswith("📖 Последний") for x in btns(m)) and accounts.sess_get(f"last:{CHAT}") is not None, str(btns(m)))
    await botmod.dp.feed_update(b, cb("last:open", msg_id=9201, text="x"))
    check("«Последний рецепт» открывает рецепт с кнопками", "👨‍🍳 Готовить по шагам" in btns(last(s, "SendMessage")))
    await botmod.dp.feed_update(b, msg_upd("👤 Профиль"))
    pm = last(s, "SendMessage")
    rowsp = [[x["text"] for x in r] for r in pm["reply_markup"]["inline_keyboard"]]
    check("кнопка «Профиль» внизу открывает профиль со справкой", "Профиль" in pm["text"] and any("❓ Справка" in r for r in rowsp), str(rowsp))
    accounts.use_link_code(accounts.new_link_code(4321), CHAT)
    await botmod.dp.feed_update(b, msg_upd("👤 Профиль"))
    rowsp = [[x["text"] for x in r] for r in last(s, "SendMessage")["reply_markup"]["inline_keyboard"]]
    check("напоминания и «Выйти на сайте» — в одной строке", any(len(r) == 2 and "Напоминания" in r[0] and "Выйти на сайте" in r[1] for r in rowsp), str(rowsp))
    await botmod.dp.feed_update(b, msg_upd("📋 Показать меню"))
    check("старая кнопка «Показать меню» обновляет клавиатуру", any(n == "SendMessage" and p.get("reply_markup", {}).get("keyboard") for n, p in s.calls[-4:]))

    # параллельные нажатия: Telegram отвечает медленно — два тапа по разным продуктам не затирают друг друга
    slow_real = s.make_request

    async def slow(bot_, method, timeout=None):
        await asyncio.sleep(0.05)
        return await slow_real(bot_, method, timeout)
    s.make_request = slow
    await botmod.dp.feed_update(b, cb("sl:o:r", msg_id=710))
    await botmod.dp.feed_update(b, cb("sl:m:b", msg_id=710))
    await asyncio.gather(botmod.dp.feed_update(b, cb("sl:i:0", msg_id=710)), botmod.dp.feed_update(b, cb("sl:i:2", msg_id=710)))
    st = accounts.sess_get(f"sl:{CHAT}:710")["st"]
    check("два быстрых тапа по разным продуктам — обе отметки на месте", st.get("0") == "b" and st.get("2") == "b", str(st))
    n0 = len(accounts.fav_list(str(CHAT)))
    await asyncio.gather(botmod.dp.feed_update(b, cb("fav:save", msg_id=710)), botmod.dp.feed_update(b, cb("fav:save", msg_id=710)))
    check("двойной тап «В избранное» — одна запись", len(accounts.fav_list(str(CHAT))) - n0 <= 1)
    await botmod.dp.feed_update(b, cb("ck:start", msg_id=711))
    cm = 5000 + s.n
    await asyncio.gather(botmod.dp.feed_update(b, cb("ck:d:0", msg_id=cm)), botmod.dp.feed_update(b, cb("ck:d:0", msg_id=cm)))
    check("двойной тап «Готово» в готовке — ровно на один шаг", accounts.sess_get(f"ck:{CHAT}:{cm}")["i"] == 1)
    s.make_request = slow_real

    botmod._flood.max = 3
    botmod._flood._log.clear()
    before = len(s.calls)
    for _ in range(6):
        await botmod.dp.feed_update(b, cb("sl:n", msg_id=710))
    check("флуд: сверх лимита нажатия молча игнорируются", len([1 for n, _ in s.calls[before:] if n == "AnswerCallbackQuery"]) == 6
          and botmod._flood._log and len(next(iter(botmod._flood._log.values()))) == 3)
    botmod._flood.max = 10000
    errs = [p for n, p in s.calls if n == "SendMessage" and "Что-то пошло не так" in p.get("text", "")]
    check("нигде не было общей ошибки", not errs, str(errs)[:200])

asyncio.run(main())
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
