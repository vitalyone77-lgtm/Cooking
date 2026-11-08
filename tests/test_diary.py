"""Дневник питания: данные, справочник, расчёт расхода, графики, сценарии бота, защита от плохого ввода."""
import asyncio
import datetime
import io
import os
import sys
from datetime import datetime as dt

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_dy.db", "accounts_dy.db", "accounts_dy.db-wal", "accounts_dy.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_dy.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_dy.db"
os.environ["WEB_DATA_DIR"] = "web_data_dy"

from PIL import Image
from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Update, CallbackQuery, Message, Chat, User

import config
import accounts
import cards
import charts
import foods
import plans
import tg_safety
tg_safety.FloodGuard.__init__.__defaults__ = (100000, 10)   # длинный сценарий одного пользователя не должен упираться в защиту от флуда
import bot as botmod
import bot_diary
from states import DiaryForm

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


U = 881001
today = datetime.date.today().isoformat()

# ---------- справочник ----------
check("в справочнике больше 200 продуктов, у каждого число и порция", len(foods.FOODS) > 200 and all(f["kcal100"] > 0 and f["grams"] > 0 for f in foods.FOODS))
check("все идентификаторы уникальны, короткие и без пробелов (идут в кнопки)", len({f["id"] for f in foods.FOODS}) == len(foods.FOODS)
      and all(len(f["id"]) <= 16 and " " not in f["id"] and f["cat"] < len(foods.CATEGORIES) for f in foods.FOODS))
check("кнопка влезает в лимит Telegram (64 байта)", all(len(f"dy:p:{f['id']}:20".encode()) <= 64 for f in foods.FOODS))
check("в каждом разделе есть продукты", all(foods.in_category(i) for i in range(len(foods.CATEGORIES))))
g = foods.BY_ID["grechka"]
check("калории считаются по граммам", foods.nutrition(g, 150)["kcal"] == 165 and foods.nutrition(g, 0)["kcal"] == 0)
check("поиск: по слову, по двум словам, с «ё», слишком короткий запрос пуст",
      any(f["id"] == "kuritsa_grud" for f in foods.search("куриная грудка")) and foods.search("борщ")[0]["id"] == "borsh"
      and foods.search("карто")[0]["cat"] == 0 and foods.search("а") == [] and foods.search("zzzz") == [])

def top(q):
    return [f["name"] for f in foods.search(q)]


check("поиск: «картошка» находит картофель", any("Картофель" in n for n in top("картошка")) and top("картошка"))
check("поиск: опечатка «греча» и «борш» находит гречку и борщ", "Гречка варёная" in top("греча") and "Борщ" in top("борш"))
check("поиск: склонение «курицу», «яйца», «сыра»", any("Курица" in n for n in top("курицу")) and "Яйцо варёное" in top("яйца") and any("Сыр" in n for n in top("сыра")))
check("поиск: «каша гречневая» и «кофе латте»", "Гречка варёная" in top("каша гречневая") and "Латте" in top("кофе латте"))
check("поиск: готовое раньше сухого («рис» → «Рис варёный»)", top("рис")[0] == "Рис варёный", str(top("рис")[:3]))
check("поиск: по синониму «сёмга», «шаверма», «пепси»", "Лосось запечённый" in top("сёмга") and "Шаурма" in top("шаверма") and "Кола и лимонад" in top("пепси"))
check("поиск: пустой, один символ, бессмыслица и инъекция — пусто, без падения", foods.search("") == [] and foods.search("а") == [] and foods.search("ъъъъъ") == [] and foods.search("'; DROP TABLE x; --") == [])
check("поиск: длинная строка не вешает", len(foods.search("к" * 5000)) >= 0)
check("больше 12 разделов в справочнике, в каждом есть продукты", len(foods.CATEGORIES) == 12 and all(foods.in_category(i) for i in range(12)))

# ---------- данные дневника ----------
check("дневник без согласия не включён", not plans.diary_on(U))
accounts.consent_set(U)
check("согласие само дневник не включает", not plans.diary_on(U))
accounts.goals_set(U, feats=["diary"])
check("после галочки дневник включён", plans.diary_on(U))
e1 = accounts.diary_add(U, "Гречка (150 г)", 165, 6.3, 1.7, 32, "list")
accounts.diary_add(U, "Яйцо", 157, 12.7, 11.5, 0.7, "list")
d = accounts.diary_day(U)
check("итог дня: калории и БЖУ складываются", d["kcal"] == 322 and d["protein"] == 19 and len(d["items"]) == 2, str(d))
check("запись удаляется, чужую удалить нельзя", accounts.diary_delete(U, e1) and not accounts.diary_delete(999, 2) and accounts.diary_day(U)["kcal"] == 157)
for bad in (dict(title="", kcal=100), dict(title="x", kcal=0), dict(title="x", kcal=999999), dict(title="x", kcal="много"), dict(title="x", kcal=100, day="вчера")):
    try:
        accounts.diary_add(U, **bad)
        ok = False
    except ValueError:
        ok = True
    check(f"плохая запись отклоняется: {bad}", ok)
long_title = accounts.diary_add(U, "а" * 500, 100) and accounts.diary_day(U)["items"][-1]["title"]
check("слишком длинное название обрезается", len(long_title) == 80)
for i in range(60):
    try:
        accounts.diary_add(U, f"запись {i}", 10)
    except ValueError:
        break
check("в день не больше 60 записей (защита от переполнения)", len(accounts.diary_day(U)["items"]) == accounts.DIARY_DAILY_MAX)

# ---------- ряды по дням и среднее ----------
V = 881002
for off, k in ((0, 1800), (1, 2000), (3, 1600)):
    accounts.diary_add(V, "еда", k, 50, 60, 200, day=(datetime.date.today() - datetime.timedelta(days=off)).isoformat())
ser = accounts.diary_series(V, 7)
check("ряд за 7 дней: по записи на каждый день, пустые дни с нулём", len(ser) == 7 and ser[-1]["kcal"] == 1800 and ser[-3]["n"] == 0 and ser[-4]["kcal"] == 1600 and ser[0]["day"] < ser[-1]["day"])
st = accounts.diary_stats(ser)
check("среднее считается только по дням с записями", st["logged"] == 3 and st["avg_kcal"] == 1800 and st["days"] == 7, str(st))
check("пустой ряд не ломает среднее", accounts.diary_stats(accounts.diary_series(881099, 5))["avg_kcal"] == 0)

# ---------- расход ----------
t = accounts.calc_tdee("m", 30, 180, 80, "sedentary")
check("расход: мужчина 30 лет, 180 см, 80 кг, сидячий — около 2 140 (Миффлин 1780 × 1,2)", 2100 <= t <= 2180, str(t))
t2 = accounts.calc_tdee("f", 30, 165, 60, "office_sport")
check("расход: женщина 30 лет, 165 см, 60 кг, офис и спорт — около 1 900", 1800 <= t2 <= 2000, str(t2))
check("активность повышает расход", accounts.calc_tdee("m", 30, 180, 80, "active") > accounts.calc_tdee("m", 30, 180, 80, "sedentary"))
for bad in (("x", 30, 180, 80, "sedentary"), ("m", 5, 180, 80, "sedentary"), ("m", 30, 50, 80, "sedentary"), ("m", 30, 180, 500, "sedentary"), ("m", 30, 180, 80, "yoga"), ("m", "", 180, 80, "sedentary")):
    try:
        accounts.calc_tdee(*bad)
        ok = False
    except ValueError:
        ok = True
    check(f"расход: неверные данные отклоняются {bad}", ok)
accounts.energy_set(U, "office_sport", 2100)
check("расход хранится только числом и уровнем активности", accounts.energy_get(U) == {"activity": "office_sport", "tdee": 2100, "activity_title": "Офис и спорт 1–3 раза в неделю"})

# ---------- графики ----------
png = charts.diary_chart(ser, 2100, [{"day": ser[0]["day"], "kg": 82.0}, {"day": ser[-1]["day"], "kg": 81.4}])
im = Image.open(io.BytesIO(png))
check("график: PNG 1200×675 со столбиками, линией расхода и линией веса", png[:8] == b"\x89PNG\r\n\x1a\n" and im.size == (1200, 675))
check("график без расхода и веса тоже рисуется", Image.open(io.BytesIO(charts.diary_chart(ser, None, None))).size == (1200, 675))
check("график пустого периода рисуется", Image.open(io.BytesIO(charts.diary_chart(accounts.diary_series(881099, 7), 2000, []))).size == (1200, 675))
check("график на 60 дней рисуется", Image.open(io.BytesIO(charts.diary_chart(accounts.diary_series(V, 60), 2000, []))).size == (1200, 675))
wpng = charts.weight_chart([{"day": "2026-10-01", "kg": 84}, {"day": "2026-11-01", "kg": 82.5}], 78)
check("график веса отдельно: PNG", Image.open(io.BytesIO(wpng)).size == (1200, 675))
check("график веса с одной записью и без записей не падает", charts.weight_chart([{"day": "2026-11-01", "kg": 80}], None)[:4] == b"\x89PNG"
      and charts.weight_chart([], None)[:4] == b"\x89PNG")
check("«Съел порцию»: калории берутся из рецепта", cards.parse("🍽 Суп", "Калорийность: 320 ккал | Б: 24 г | Ж: 9 г | У: 36 г")["kcal"] == "320")
check("своё блюдо: «пицца 450» разбирается, мусор нет",
      bot_diary.parse_manual("пицца 450") == ("пицца", 450) and bot_diary.parse_manual("борщ с мясом 380 ккал") == ("борщ с мясом", 380)
      and bot_diary.parse_manual("просто текст") is None and bot_diary.parse_manual("1") is None)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls, self.n = [], 0

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append((name, method.model_dump(exclude_none=True)))
        if name in ("SendMessage", "EditMessageText", "SendPhoto", "EditMessageMedia"):
            self.n += 1
            return Message(message_id=9900 + self.n, date=dt.now(), chat=Chat(id=C, type="private"), text=getattr(method, "text", "") or "").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


C = 881010
user = User(id=C, is_bot=False, first_name="T")
chat = Chat(id=C, type="private")
seq = [0]
RECIPE = "🍽 Куриный суп с лапшой\n\n⏱ Время: 30 мин | 👥 Порций: 2\n\nКалорийность: 320 ккал | Б: 24 г | Ж: 9 г | У: 36 г\n\n*Продукты:*\n- Курица"


def cb(data, text="x"):
    seq[0] += 1
    m = Message(message_id=700, date=dt.now(), chat=chat, text=text)
    return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=user, chat_instance="x", message=m, data=data))


def cbp(data):
    from aiogram.types import PhotoSize
    seq[0] += 1
    m = Message(message_id=700, date=dt.now(), chat=chat, photo=[PhotoSize(file_id="x", file_unique_id="y", width=1, height=1)])
    return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=user, chat_instance="x", message=m, data=data))


def msg(text):
    seq[0] += 1
    return Update(update_id=seq[0], message=Message(message_id=100 + seq[0], date=dt.now(), chat=chat, from_user=user, text=text))


def btns(p):
    return [x["text"] for r in (p or {}).get("reply_markup", {}).get("inline_keyboard", []) for x in r]


def datas(p):
    return [x["callback_data"] for r in (p or {}).get("reply_markup", {}).get("inline_keyboard", []) for x in r]


async def main():
    import ui
    from aiogram.types import PhotoSize
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    st = botmod.dp.fsm.get_context(b, C, C)
    last = lambda name: next((p for n, p in reversed(s.calls) if n == name), None)
    SCREENS = ("SendMessage", "EditMessageText", "SendPhoto", "EditMessageMedia")

    def screen(sess=None):
        sess = sess or s
        return next((p for n, p in reversed(sess.calls) if n in SCREENS), {})

    def stext(sess=None):
        p = screen(sess)
        return p.get("text") or p.get("caption") or (p.get("media") or {}).get("caption", "") if isinstance(p.get("media"), dict) else (p.get("text") or p.get("caption") or "")

    def sends(sess=None, since=0):
        return len([1 for n, _ in (sess or s).calls[since:] if n in ("SendMessage", "SendPhoto")])

    # дневник по умолчанию выключен и ничего не пишет
    await botmod.dp.feed_update(b, cb("dy:open"))
    check("без галочки дневник не открывается, понятный отказ", last("AnswerCallbackQuery").get("show_alert") is True and not accounts.diary_day(C)["items"])
    await botmod.dp.feed_update(b, cb("dy:p:grechka:10"))
    check("без галочки запись не делается", accounts.diary_day(C)["kcal"] == 0)

    # включение: согласие, затем «Функции»
    await botmod.dp.feed_update(b, cb("pf:consent"))
    await botmod.dp.feed_update(b, cb("pf:fts"))
    m = last("EditMessageText")
    check("в «Функциях» дневник стоит первым и выключен", btns(m)[0] == "⬜ 📒 Дневник питания" and "pf:ft:diary" in datas(m), str(btns(m)))
    await botmod.dp.feed_update(b, cb("pf:ft:diary"))
    check("галочка включает дневник", plans.diary_on(C) and btns(last("EditMessageText"))[0].startswith("✅"))

    # главный экран профиля при включённом дневнике
    await botmod.dp.feed_update(b, cb("pf:main"))
    m = last("EditMessageText")
    check("профиль без записей: текст «Сегодня: 0 ккал», подсказка про график", "📒 Сегодня: 0 ккал" in m["text"] and "График появится" in m["text"], m["text"][:200])
    check("в профиле три ряда кнопок без повторов", btns(m) == ["📒 Записать еду", "⚖️ Вес", "📅 30 дн. ▸ 90", "⚙️ Настройки"], str(btns(m)))
    check("в профиле нет текстового «рисунка» веса", "▁" not in m["text"] and "▇" not in m["text"])
    await botmod.dp.feed_update(b, cb("pf:set"))
    m = last("EditMessageText")
    check("настройки: прежние кнопки профиля, расход, возврат в профиль", {"🚫 Исключить продукты", "🎯 Цели и вес", "🧩 Функции", "🌐 Без VPN", "🗑 Удалить", "⬅️ Профиль"} <= set(btns(m))
          and "📒 Дневник" not in btns(m) and "Настройки" in m["text"], str(btns(m)))
    await botmod.dp.feed_update(b, cb("pf:back"))
    check("«назад» из подразделов ведёт в настройки, а не в график", "Настройки" in last("EditMessageText")["text"])

    # экран дневника и запись из списка
    n_send0 = sends()
    await botmod.dp.feed_update(b, cb("dy:open"))
    m = last("EditMessageText")
    check("дневник: оговорка, расход не задан, кнопки без повторов", "Не врач и не диетолог" in m["text"] and "Расход не задан" in m["text"]
          and {"➕ Из списка", "🔎 Найти", "🍽 Записать рецепт", "🧮 Расход", "⬅️ Профиль"} <= set(btns(m)) and not any("График" in x for x in btns(m)), str(btns(m)))
    await botmod.dp.feed_update(b, cb("dy:cat"))
    check("разделы справочника кнопками", set(foods.CATEGORIES) <= set(btns(last("EditMessageText"))))
    await botmod.dp.feed_update(b, cb("dy:c:0"))
    m = last("EditMessageText")
    check("в разделе продукты с калориями порции", any("Гречка варёная · 165" in t for t in btns(m)), str(btns(m))[:200])
    await botmod.dp.feed_update(b, cb("dy:f:grechka"))
    m = last("EditMessageText")
    rows_ = m["reply_markup"]["inline_keyboard"]
    check("порции: ложки с горкой строкой, Малая, Стандарт, Большая строкой, своё количество", [x["text"] for x in rows_[0]] == ["1 ст. л.", "2 ст. л.", "3 ст. л.", "4 ст. л."]
          and [x["text"] for x in rows_[1]] == ["Малая", "Стандарт", "Большая"] and rows_[2][0]["text"] == "✏️ Своё количество"
          and "Малая: 105 г" in m["text"] and "Стандарт: 150 г ≈ 165 ккал" in m["text"] and "Большая: 225 г ≈ 248 ккал" in m["text"], m["text"])
    await botmod.dp.feed_update(b, cb("dy:p:grechka:999"))
    check("выдуманная доля порции не записывается", accounts.diary_day(C)["kcal"] == 0)
    await botmod.dp.feed_update(b, cb("dy:p:grechka:15"))
    check("«1½»: записано 248 ккал", accounts.diary_day(C)["kcal"] == 248 and "Гречка варёная (большая порция, 225 г)" in accounts.diary_day(C)["items"][0]["title"], str(accounts.diary_day(C)))
    check("после записи тот же экран показывает итог", "Съедено: 248 ккал" in last("EditMessageText")["text"])
    check("весь путь профиль → список → порция прошёл без единого нового сообщения", sends(since=0) == n_send0, str(sends()))
    await botmod.dp.feed_update(b, cb("dy:cat"))
    check("после первой записи продукт стал «частым» и стоит наверху", btns(last("EditMessageText"))[0].startswith("⭐ Гречка варёная"), str(btns(last("EditMessageText"))[:2]))

    await botmod.dp.feed_update(b, cb("dy:g:borsh"))
    check("«свои граммы»: ждём число", await st.get_state() == DiaryForm.grams.state)
    n_send1 = sends()
    n_del = len([1 for n, _ in s.calls if n == "DeleteMessage"])
    await botmod.dp.feed_update(b, msg("много"))
    check("не число — подсказка на том же экране, ждём дальше", await st.get_state() == DiaryForm.grams.state and "не понял количество" in last("EditMessageText")["text"])
    check("ответ человека удалён из чата, новых сообщений нет", len([1 for n, _ in s.calls if n == "DeleteMessage"]) == n_del + 1 and sends() == n_send1)
    await botmod.dp.feed_update(b, msg("99999"))
    check("нелепый вес отклоняется", await st.get_state() == DiaryForm.grams.state and accounts.diary_day(C)["kcal"] == 248)
    await botmod.dp.feed_update(b, msg("400"))
    check("400 г борща: 200 ккал, итог 448, экран тот же", accounts.diary_day(C)["kcal"] == 448 and await st.get_state() is None
          and "Съедено: 448 ккал" in last("EditMessageText")["text"] and sends() == n_send1)

    # поиск и своё блюдо
    await botmod.dp.feed_update(b, cb("dy:find"))
    import ai as _ai
    _real_llm = _ai.call_llm_with_fallback

    async def _llm_bad(messages, max_tokens=3000):
        return "мусор"
    _ai.call_llm_with_fallback = _llm_bad
    await botmod.dp.feed_update(b, msg("zzzz"))
    check("поиск: нет в справочнике, ИИ не смог — совет и можно написать ещё раз", "записать" in last("EditMessageText")["text"].lower() or "запишите" in last("EditMessageText")["text"].lower())
    check("поиск: состояние поиска остаётся", await st.get_state() == DiaryForm.find.state)

    async def _llm_ok(messages, max_tokens=3000):
        return '{"food":true,"name":"Шаурма","grams":300,"label":"1 шт","kcal100":210,"protein100":11,"fat100":10,"carb100":18}'
    _ai.call_llm_with_fallback = _llm_ok
    await botmod.dp.feed_update(b, msg("шаурма дворовая"))
    t = last("EditMessageText")
    check("поиск: ИИ оценил, кнопки порции и пометка", "Оценка ИИ" in t["text"] and "Шаурма" in t["text"], t["text"])
    check("поиск: кнопки порции ведут на запись", any(("dy:p:a" in x.callback_data or "dy:u:a" in x.callback_data) for r in t["reply_markup"].inline_keyboard for x in r), str(t.get("reply_markup"))[:200]) if not isinstance(t.get("reply_markup"), dict) else check("поиск: кнопки порции ведут на запись", "dy:p:a" in str(t["reply_markup"]) or "dy:u:a" in str(t["reply_markup"]))
    _ai.call_llm_with_fallback = _real_llm
    await botmod.dp.feed_update(b, cb("dy:find"))
    await botmod.dp.feed_update(b, msg("куриная грудка"))
    check("поиск: нашёл продукт кнопкой", any("Куриная грудка варёная" in t for t in btns(last("EditMessageText"))))
    await botmod.dp.feed_update(b, cb("dy:find"))
    await botmod.dp.feed_update(b, msg("пицца 450"))
    check("своё блюдо «пицца 450» записано", accounts.diary_day(C)["kcal"] == 898 and accounts.diary_day(C)["items"][-1]["src"] == "manual")

    # удаление записи
    items = accounts.diary_day(C)["items"]
    await botmod.dp.feed_update(b, cb(f"dy:del:{items[-1]['id']}"))
    check("запись удаляется кнопкой", accounts.diary_day(C)["kcal"] == 448)
    await botmod.dp.feed_update(b, cb("dy:del:999999"))
    check("удаление несуществующей записи не ломает бота", accounts.diary_day(C)["kcal"] == 448)
    for bad in ("dy:del:abc", "dy:f:нет-такого", "dy:p:grechka:zzz", "dy:c:99", "pf:p:abc", "pf:p:5", "dy:ch:zz", "dy:ch:9999"):
        await botmod.dp.feed_update(b, cb(bad))
    check("испорченные кнопки не роняют бота и ничего не записывают", accounts.diary_day(C)["kcal"] == 448)

    # «Съел порцию» под рецептом: только всплывающее окно
    n_send2 = sends()
    await botmod.dp.feed_update(b, cb("dy:eat", text=RECIPE))
    d = accounts.diary_day(C)
    toast = last("AnswerCallbackQuery")
    check("«Съел порцию»: записана порция именно этого рецепта с КБЖУ", d["kcal"] == 768 and d["items"][-1]["title"] == "Куриный суп с лапшой" and d["items"][-1]["protein"] == 24, str(d["items"][-1]))
    check("подтверждение всплывающим окном, чат не засорён", toast.get("show_alert") is True and "320 ккал" in toast["text"] and sends() == n_send2)
    await botmod.dp.feed_update(b, cb("dy:eat", text="Просто сообщение без калорий"))
    check("нет рецепта с калориями — понятный отказ", last("AnswerCallbackQuery").get("show_alert") is True and accounts.diary_day(C)["kcal"] == 768)
    kb_ = botmod.recipe_kb(RECIPE, C)
    check("под рецептом появилась кнопка «Съел порцию», у выключивших её нет", any(x.text == "🍽 Записать в дневник" for r in kb_.inline_keyboard for x in r)
          and not any(x.text == "🍽 Записать в дневник" for r in botmod.recipe_kb(RECIPE, 999).inline_keyboard for x in r)
          and not any(x.text == "🍽 Записать в дневник" for r in botmod.recipe_kb(RECIPE).inline_keyboard for x in r))

    # расчёт расхода
    await botmod.dp.feed_update(b, cb("dy:calc"))
    await botmod.dp.feed_update(b, cb("dy:s:m"))
    n_send3 = sends()
    check("после выбора пола ждём одно сообщение с тремя числами", await st.get_state() == DiaryForm.body.state and "38 175 82" in last("EditMessageText")["text"])
    for bad, frag in (("сорок", "три числа"), ("38 175", "три числа"), ("5 175 80", "Возраст"), ("38 500 80", "Рост"), ("38 175 900", "Вес"), ("38 175 82 1", "три числа")):
        await botmod.dp.feed_update(b, msg(bad))
        check(f"плохой ввод «{bad}» отклонён с подсказкой", await st.get_state() == DiaryForm.body.state and frag in last("EditMessageText")["text"], last("EditMessageText")["text"][:80])
    await botmod.dp.feed_update(b, msg("30 180 80"))
    check("три числа приняты, спрашиваем активность", await st.get_state() == DiaryForm.activity.state and "активность" in last("EditMessageText")["text"])
    await botmod.dp.feed_update(b, cb("dy:a:office_sport"))
    en = accounts.energy_get(C)
    res = last("EditMessageText")["text"]
    check("расход посчитан (1780 × 1,375 ≈ 2450) и сохранён", bool(en) and 2400 <= en["tdee"] <= 2500, str(en))
    check("в ответе оговорка и нет советов «сколько есть, чтобы худеть»", "не врач" in res.lower() and "снижается" not in res and "0,85" not in res and "не советуем" in res, res[:300])
    check("весь расчёт прошёл на одном экране, новых сообщений нет", sends() == n_send3)
    check("возраст, рост и пол нигде не сохранились", set(en) == {"activity", "tdee", "activity_title"} and await st.get_state() is None)
    await botmod.dp.feed_update(b, cb("dy:a:office_sport"))
    check("повторное нажатие без данных не падает", True)

    # профиль-график: картинка, периоды 30 → 90 → 7, правка картинки на месте
    accounts.weight_set(C, 82.0, (datetime.date.today() - datetime.timedelta(days=25)).isoformat())
    accounts.weight_set(C, 81.6, (datetime.date.today() - datetime.timedelta(days=12)).isoformat())
    accounts.weight_set(C, 81.2, today)
    g0 = accounts.goals_get(C)
    accounts.goals_set(C, kcal=2000, feats=g0["feats"] + ["macro"])
    n_ph = len([1 for n, _ in s.calls if n == "SendPhoto"])
    await botmod.dp.feed_update(b, cb("pf:main"))
    ph = [p for n, p in s.calls if n == "SendPhoto"]
    cap = ph[-1]["caption"]
    check("профиль теперь картинка с графиком и короткой подписью (до 1024 знаков)", len(ph) == n_ph + 1 and len(cap) <= 1024 and "В среднем" in cap and "⚖️ Вес 81.2" in cap
          and "Не врач" in cap.replace("не врач", "Не врач"), cap)
    check("в подписи под графиком есть цель на день", "🎯 Цель на день: 2000 ккал" in cap, cap)
    check("кнопка периода предлагает следующий: 30 → 90", "📅 30 дн. ▸ 90" in btns(ph[-1]))
    check("при смене вида старое текстовое сообщение удалено", any(n == "DeleteMessage" for n, _ in s.calls[-4:]))
    n_ph2 = len([1 for n, _ in s.calls if n == "SendPhoto"])
    await botmod.dp.feed_update(b, cbp("pf:p:90"))
    mm = last("EditMessageMedia")
    check("смена периода правит картинку на месте (edit_media), без нового сообщения", mm is not None and len([1 for n, _ in s.calls if n == "SendPhoto"]) == n_ph2)
    check("после 90 предлагается 7", any("📅 90 дн. ▸ 7" in x for x in btns(mm)), str(btns(mm)))
    await botmod.dp.feed_update(b, cbp("pf:p:7"))
    check("неделя тоже рисуется", last("EditMessageMedia") is not None and any("📅 7 дн. ▸ 30" in x for x in btns(last("EditMessageMedia"))))
    await botmod.dp.feed_update(b, cbp("dy:open"))
    check("из картинки в дневник: картинка удалена, пришёл текстовый экран", any(n == "DeleteMessage" for n, _ in s.calls[-3:]) and "📒 Дневник питания" in stext())

    # без дневника, но с весом: график веса; без всего: отказ
    D = 881020
    accounts.consent_set(D)
    s2 = FakeSession()
    b2 = Bot(token="42:TEST", session=s2)

    def cb2(data):
        seq[0] += 1
        m = Message(message_id=701, date=dt.now(), chat=Chat(id=D, type="private"), text="x")
        return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=User(id=D, is_bot=False, first_name="D"), chat_instance="x", message=m, data=data))
    await botmod.dp.feed_update(b2, cb2("dy:ch:14"))
    check("нечего показывать — понятный отказ без картинки", not [1 for n, _ in s2.calls if n == "SendPhoto"])
    accounts.weight_set(D, 70, (datetime.date.today() - datetime.timedelta(days=3)).isoformat())
    accounts.weight_set(D, 69.5, today)
    await botmod.dp.feed_update(b2, cb2("dy:ch:14"))
    ph = [p for n, p in s2.calls if n == "SendPhoto"]
    check("только вес: приходит график веса картинкой", len(ph) == 1 and "веса" in ph[0]["caption"])

    # чужие данные недоступны, удаление всё стирает
    check("записи видны только владельцу", accounts.diary_day(D)["kcal"] == 0 and accounts.diary_day(C)["kcal"] == 768)
    accounts.delete_my_data(C)
    check("«Удалить мои данные» стирает дневник и расход", accounts.diary_day(C)["items"] == [] and accounts.energy_get(C) is None and not plans.diary_on(C))

    # командой /profile: прошлый экран убирается, новый приходит внизу
    s5 = FakeSession()
    b5 = Bot(token="42:TEST", session=s5)
    await botmod.dp.feed_update(b5, msg("/profile"))
    n1 = sends(s5)
    await botmod.dp.feed_update(b5, msg("/profile"))
    check("повторный /profile: прошлый экран удалён, а не копится", sends(s5) == n1 + 1 and any(n == "DeleteMessage" for n, _ in s5.calls))

    # потерянное состояние (бот перезапускался посреди расчёта): объяснение вместо меню
    F = 881030
    accounts.consent_set(F)
    accounts.goals_set(F, feats=["diary"])
    s6 = FakeSession()
    b6 = Bot(token="42:TEST", session=s6)
    user6, chat6 = User(id=F, is_bot=False, first_name="F"), Chat(id=F, type="private")

    def msg6(text):
        seq[0] += 1
        return Update(update_id=seq[0], message=Message(message_id=100 + seq[0], date=dt.now(), chat=chat6, from_user=user6, text=text))
    await botmod.dp.feed_update(b6, msg6("38 175 82"))
    check("без начатого расчёта лишний текст получает обычное меню", "Не понял запрос" in next(p for n, p in s6.calls if n == "SendMessage")["text"])
    accounts.sess_set(f"dycalc:{F}", {"step": "body"})
    await botmod.dp.fsm.get_context(b6, F, F).clear()          # как после перезапуска бота: состояния нет
    s6.calls.clear()
    await botmod.dp.feed_update(b6, msg6("38 175 82"))
    m6 = next(p for n, p in s6.calls if n == "SendMessage")
    check("расчёт прерван перезапуском: понятное объяснение и кнопка начать заново", "прервался" in m6["text"] and "dy:calc" in datas(m6), str(m6)[:200])
    s6.calls.clear()
    await botmod.dp.fsm.get_context(b6, F, F).clear()
    await botmod.dp.feed_update(b6, msg6("ещё текст"))
    check("повторно объяснение не навязывается", "Не понял запрос" in next(p for n, p in s6.calls if n == "SendMessage")["text"])

    # график на 90 дней и недельные средние
    ser = accounts.diary_series(V, 90)
    png90 = charts.diary_chart(ser, 2000, [{"day": ser[0]["day"], "kg": 83.0}, {"day": ser[45]["day"], "kg": 82.0}, {"day": ser[-1]["day"], "kg": 81.5}])
    check("график на 90 дней (недельные средние, тренд веса) рисуется", Image.open(io.BytesIO(png90)).size == (1200, 675))
    ser91 = accounts.diary_series(881099, 90)
    check("график на 90 дней без записей и без веса тоже рисуется", Image.open(io.BytesIO(charts.diary_chart(ser91, None, []))).size == (1200, 675))


asyncio.run(main())
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
