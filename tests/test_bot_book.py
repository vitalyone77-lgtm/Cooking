"""«📖 Книга рецептов» в боте: разделы, условия, хештеги, слово, просмотр рецепта с карточкой, избранное, испорченные кнопки, без спама."""
import asyncio
import os
import sys
from datetime import datetime as dt

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_bb.db", "accounts_bb.db", "accounts_bb.db-wal", "accounts_bb.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_bb.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_bb.db"
os.environ["WEB_DATA_DIR"] = "web_data_bb"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Update, CallbackQuery, Message, Chat, User

import tg_safety
tg_safety.FloodGuard.__init__.__defaults__ = (100000, 10)
import accounts
import book
import favorites
import bot as botmod
from last_recipe import get_last_recipe
from states import BookForm

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


def recipe(title, minutes=25, kcal=350, protein=24, food="Курица — 200 г\n- Гречка — 80 г", servings=2):
    return (f"🍽 {title}\n\n⏱ Время: {minutes} мин | 👥 Порций: {servings} | 🔥 Способ: Плита\n\n"
            f"Калорийность: {kcal} ккал | Б: {protein} г | Ж: 9 г | У: 36 г\n\n*Продукты:*\n- {food}\n- Соль, перец\n\n"
            "*Инструкция:*\n1. Подготовьте продукты и разогрейте сковороду.\n2. Обжарьте основной продукт до готовности и подавайте горячим.\n"
            "3. Проверьте готовность самостоятельно. Приятного аппетита!\n\n" + "Подробности приготовления. " * 6)


D = {"cuisine": "classic", "macro_goal": "", "preferred": "", "excluded": "", "time": "30 мин", "servings": 2, "portion": "normal",
     "appliance": ["stove"], "appliance_labels": ["Плита"]}
book.add(D, "Овсяная каша", recipe("Овсяная каша", 10, 250, 8, "Овсянка — 60 г\n- Молоко — 200 мл"), ["овсянка"])
book.add(D, "Борщ красный", recipe("Борщ красный", 60, 300, 15, "Говядина — 300 г\n- Свёкла — 200 г"), ["говядина"])
book.add(D, "Куриная грудка с гречкой", recipe("Куриная грудка с гречкой", 25, 380, 40), ["курица"])
book.add({**D, "appliance": ["oven"]}, "Запечённая курица", recipe("Запечённая курица", 50, 420, 38, "Курица — 600 г"), ["курица"])
book.add(D, "Шоколадный мусс", recipe("Шоколадный мусс", 20, 280, 6, "Шоколад — 50 г\n- Сливки — 100 мл"), ["шоколад"])
book.add({**D, "servings": 4}, "Паста с говядиной", recipe("Паста с говядиной", 30, 520, 28, "Макароны — 200 г\n- Говядина — 200 г", 4), ["макароны"])
for i in range(12):
    book.add(D, f"Рагу овощное №{i}", recipe(f"Рагу овощное №{i}", 40, 200, 5, f"Кабачок — {100 + i} г\n- Морковь — 100 г"), ["кабачок"])


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
            return Message(message_id=9300 + self.n, date=dt.now(), chat=Chat(id=1, type="private"), text=getattr(method, "text", "") or "x").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


C = 792001
seq = [0]
user, chat = User(id=C, is_bot=False, first_name="T"), Chat(id=C, type="private")


def cb(data):
    seq[0] += 1
    m = Message(message_id=700, date=dt.now(), chat=chat, text="x")
    return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=user, chat_instance="x", message=m, data=data))


def msg(text):
    seq[0] += 1
    return Update(update_id=seq[0], message=Message(message_id=100 + seq[0], date=dt.now(), chat=chat, from_user=user, text=text))


def btns(p):
    return [x["text"] for r in (p or {}).get("reply_markup", {}).get("inline_keyboard", []) for x in r]


def datas(p):
    return [x["callback_data"] for r in (p or {}).get("reply_markup", {}).get("inline_keyboard", []) for x in r]


async def main():
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    st = botmod.dp.fsm.get_context(b, C, C)
    last = lambda name: next((p for n, p in reversed(s.calls) if n == name), None)
    sends = lambda: len([1 for n, _ in s.calls if n in ("SendMessage", "SendPhoto")])
    ui_n0 = sends()

    await botmod.dp.feed_update(b, cb("book:open"))
    m = next(p for n, p in reversed(s.calls) if n in ("SendMessage", "EditMessageText"))
    check("главная книги: сколько рецептов, пополняется каждый день, без лимитов", f"{book.total()} рецептов" in m["text"] and "каждый день" in m["text"] and "без ограничений" in m["text"], m["text"][:300])
    check("кнопки: разделы со счётчиками, условия, хештеги, слово, избранное, меню", {"🌅 Завтраки (1)", "🍲 Обеды (13)", "📚 Все рецепты", "⚙️ Подобрать по условиям", "🏷 Хештеги", "🔎 Найти по слову", "⭐ Моё избранное (0)", "⬅️ Меню"} <= set(btns(m)), str(btns(m)))
    check("главное меню бота теперь ведёт в книгу", "book:open" in [x.callback_data for r in botmod.kb.cuisine_kb().inline_keyboard for x in r])

    await botmod.dp.feed_update(b, cb("bk:m:ужин"))
    m = last("EditMessageText")
    check("раздел «Ужины»: список с калориями и временем", "Найдено: " in m["text"] and any("Куриная грудка с гречкой · 380 ккал · 25 мин" in x for x in btns(m)), str(btns(m))[:300])
    await botmod.dp.feed_update(b, cb("bk:m:all"))
    m = last("EditMessageText")
    total = book.total()
    check("«Все рецепты»: страницы по 8, есть переход вперёд", f"Найдено: {total}" in m["text"] and "▶️" in btns(m) and "◀️" not in btns(m), m["text"][:200])
    await botmod.dp.feed_update(b, cb("bk:l:1"))
    m = last("EditMessageText")
    check("вторая страница: есть «назад»", "◀️" in btns(m) and "страница 2" in m["text"])
    await botmod.dp.feed_update(b, cb("bk:l:99"))
    check("слишком далёкая страница не ломает, показывает последнюю", "страница" in last("EditMessageText")["text"])

    # условия
    await botmod.dp.feed_update(b, cb("bk:fl"))
    m = last("EditMessageText")
    check("условия: шесть пунктов и «Показать»", {"🍽 Раздел: все", "🔥 Техника: любая", "👥 Порции: любые", "🥩 Основа: любая", "⚡ Калории: любые", "⏱ Время: любое"} <= set(btns(m)), str(btns(m)))
    await botmod.dp.feed_update(b, cb("bk:f:app"))
    check("выбор техники: плита, духовка, мультиварка, аэрогриль", {"Плита", "Духовка", "Мультиварка", "Аэрогриль"} <= {x.replace("✅ ", "") for x in btns(last("EditMessageText"))})
    await botmod.dp.feed_update(b, cb("bk:o:app:2"))
    m = last("EditMessageText")
    check("выбрано «Духовка»: счётчик подходящих обновился", "🔥 Техника: Духовка" in btns(m) and "✅ Показать (1)" in btns(m), str(btns(m)))
    await botmod.dp.feed_update(b, cb("bk:l:0"))
    m = last("EditMessageText")
    check("показан запечённый рецепт под духовку", any("Запечённая курица" in x for x in btns(m)) and "техника: духовка" in m["text"], m["text"])
    await botmod.dp.feed_update(b, cb("bk:x"))
    await botmod.dp.feed_update(b, cb("bk:f:main"))
    await botmod.dp.feed_update(b, cb("bk:o:main:1"))   # говядина
    await botmod.dp.feed_update(b, cb("bk:l:0"))
    m = last("EditMessageText")
    check("основной ингредиент «Говядина»: борщ и паста", {"Борщ красный", "Паста с говядиной"} <= {x.split(" ·")[0].replace("🍽 ", "") for x in btns(m)}, str(btns(m)))
    await botmod.dp.feed_update(b, cb("bk:fl"))
    await botmod.dp.feed_update(b, cb("bk:f:srv"))
    await botmod.dp.feed_update(b, cb("bk:o:srv:4"))    # 4 порции
    await botmod.dp.feed_update(b, cb("bk:l:0"))
    m = last("EditMessageText")
    check("говядина и 4 порции: только паста", [x for x in btns(m) if x.startswith("🍽")] == ["🍽 Паста с говядиной · 520 ккал · 30 мин"], str(btns(m)))
    await botmod.dp.feed_update(b, cb("bk:fl"))
    await botmod.dp.feed_update(b, cb("bk:f:kcal"))
    await botmod.dp.feed_update(b, cb("bk:o:kcal:1"))   # до 300
    await botmod.dp.feed_update(b, cb("bk:l:0"))
    m = last("EditMessageText")
    check("добавили «до 300 ккал»: ничего нет, понятная подсказка и путь «Создать»", "Найдено: 0" in m["text"] and "«Создать»" in m["text"], m["text"])
    await botmod.dp.feed_update(b, cb("bk:fl"))
    await botmod.dp.feed_update(b, cb("bk:x"))
    check("«Сбросить» очищает условия", "✅ Показать (" + str(total) + ")" in btns(last("EditMessageText")), str(btns(last("EditMessageText"))))

    # слово и хештеги
    await botmod.dp.feed_update(b, cb("bk:q"))
    check("поиск по слову: ждём текст", await st.get_state() == BookForm.q.state)
    n_send = sends()
    n_del = len([1 for n, _ in s.calls if n == "DeleteMessage"])
    await botmod.dp.feed_update(b, msg("говядина"))
    m = last("EditMessageText")
    check("слово «говядина»: найдены борщ и паста, ответ человека удалён, нового сообщения нет",
          "«говядина»" in m["text"] and "Найдено: 2" in m["text"] and len([1 for n, _ in s.calls if n == "DeleteMessage"]) == n_del + 1 and sends() == n_send, m["text"])
    await botmod.dp.feed_update(b, cb("bk:q"))
    await botmod.dp.feed_update(b, msg("-"))
    check("«-» убирает слово", "«" not in last("EditMessageText")["text"].split("\n")[0])
    await botmod.dp.feed_update(b, cb("bk:q"))
    await botmod.dp.feed_update(b, msg("'; DROP TABLE book; --"))
    check("странный текст не ломает книгу", book.total() == total and "Найдено: 0" in last("EditMessageText")["text"])
    await botmod.dp.feed_update(b, cb("bk:tg"))
    m = last("EditMessageText")
    check("хештеги со счётчиками", any(x.startswith("#ужин (") for x in btns(m)) and any(x.startswith("#быстро (") for x in btns(m)), str(btns(m)))
    idx = [x for x in btns(m) if x.startswith("#быстро")][0]
    await botmod.dp.feed_update(b, cb(f"bk:t:{[x for x in btns(m)].index(idx)}"))
    m = last("EditMessageText")
    check("выбран хештег: список только с ним", "#быстро" in m["text"], m["text"][:200])
    await botmod.dp.feed_update(b, cb("bk:fl"))
    await botmod.dp.feed_update(b, cb("bk:x"))

    # просмотр рецепта
    rid = book.search(q="борщ")[0][0]["id"]
    n_ph = len([1 for n, _ in s.calls if n == "SendPhoto"])
    await botmod.dp.feed_update(b, cb(f"bk:v:{rid}"))
    ph = [p for n, p in s.calls if n == "SendPhoto"]
    sm = last("SendMessage")
    check("рецепт из книги: сначала карточка-картинка, затем текст с хештегами и обычными кнопками", len(ph) == n_ph + 1 and "Борщ красный" in sm["text"] and "#обед" in sm["text"]
          and {"📖 К книге", "🛒 Продукты и магазины", "⭐ В избранное"} <= set(btns(sm)), str(btns(sm)))
    check("рецепт стал «последним»: работают готовка и список покупок", (get_last_recipe(C) or {}).get("title") == "Борщ красный")
    check("рецепт отмечен показанным: бот не предложит его снова в течение двух недель", book.find(C, {**D, "preferred": "борщ", "time": "Не важно"}) is None)

    # избранное
    await botmod.dp.feed_update(b, cb("bk:fav:0"))
    check("пустое избранное: подсказка", "пока пусто" in last("EditMessageText")["text"])
    favorites.add_favorite(C, "Сырники", recipe("Сырники"), "classic")
    await botmod.dp.feed_update(b, cb("bk:fav:0"))
    m = last("EditMessageText")
    check("избранное: список и просмотр", any("Сырники" in x for x in btns(m)) and any(d.startswith("fav:view:") for d in datas(m)) and "📖 Книга рецептов" in btns(m))
    await botmod.dp.feed_update(b, cb("favorites:open"))
    check("«⭐ Избранное» открывает список своих рецептов, а рядом отдельная кнопка книги", "Моё избранное" in last("EditMessageText")["text"] or "Моё избранное" in last("SendMessage")["text"])
    labels = [x.text for r in botmod.kb.cuisine_kb().inline_keyboard for x in r]
    check("в меню рядом стоят «⭐ Избранное» и «📖 Книга рецептов»", labels.index("📖 Книга рецептов") == labels.index("⭐ Избранное") + 1, str(labels))

    # испорченные кнопки
    for bad in ("bk:o:app:99", "bk:o:zzz:1", "bk:f:xxx", "bk:v:abc", "bk:v:999999", "bk:l:zzz", "bk:t:99", "bk:m:zzz", "bk:fav:zz", "bk:o:app", "bk:t:"):
        await botmod.dp.feed_update(b, cb(bad))
    check("испорченные кнопки не роняют бота", book.total() == total)

    # весь путь по книге без нового сообщения (кроме самого рецепта)
    s2 = FakeSession()
    b2 = Bot(token="42:TEST", session=s2)
    C2 = 792002
    chat2, user2 = Chat(id=C2, type="private"), User(id=C2, is_bot=False, first_name="T2")

    def cb2(data):
        seq[0] += 1
        m = Message(message_id=701, date=dt.now(), chat=chat2, text="x")
        return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=user2, chat_instance="x", message=m, data=data))
    for d in ("bk:h", "bk:m:ужин", "bk:fl", "bk:f:app", "bk:o:app:1", "bk:fl", "bk:f:time", "bk:o:time:2", "bk:l:0", "bk:tg", "bk:h", "bk:fav:0", "bk:h"):
        await botmod.dp.feed_update(b2, cb2(d))
    check("весь путь по книге (13 нажатий) без единого нового сообщения", not [1 for n, _ in s2.calls if n in ("SendMessage", "SendPhoto")], str([n for n, _ in s2.calls if n in ("SendMessage", "SendPhoto")]))

asyncio.run(main())
print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
