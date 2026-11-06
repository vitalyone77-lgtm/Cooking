"""Книга рецептов: разделы и хештеги, наполнение, подбор из книги с учётом условий, повторы, бот и сайт."""
import asyncio
import datetime
import os
import sys
import time
from datetime import datetime as dt

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_bk.db", "accounts_bk.db", "accounts_bk.db-wal", "accounts_bk.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_bk.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_bk.db"
os.environ["WEB_DATA_DIR"] = "web_data_bk"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Update, CallbackQuery, Message, Chat, User
from fastapi.testclient import TestClient

import tg_safety
tg_safety.FloodGuard.__init__.__defaults__ = (100000, 10)
import config
import accounts
import book
import bot as botmod
import web_api
import web_db
from states import RecipeForm

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


def recipe(title, minutes=25, kcal=350, protein=24, food="Курица — 200 г\n- Гречка — 80 г", servings=2):
    return (f"🍽 {title}\n\n⏱ Время: {minutes} мин | 👥 Порций: {servings} | 🔥 Способ: Плита\n\n"
            f"Калорийность: {kcal} ккал | Б: {protein} г | Ж: 9 г | У: 36 г\n\n*Продукты:*\n- {food}\n- Соль, перец\n\n"
            "*Инструкция:*\n1. Подготовьте продукты и разогрейте сковороду.\n2. Обжарьте основной продукт до готовности и подавайте горячим.\n"
            "3. Проверьте готовность самостоятельно: прожарка зависит от плиты и толщины кусков. Приятного аппетита!\n\n" + "Подробности приготовления. " * 6)


DATA = {"cuisine": "classic", "macro_goal": "", "preferred": "", "excluded": "", "time": "30 мин", "servings": 2, "portion": "normal",
        "appliance": ["stove"], "appliance_labels": ["Плита"]}

# ---------- разделы и хештеги ----------
cases = {"Овсяная каша с яблоком": "завтрак", "Сырники из творога": "завтрак", "Борщ красный": "обед", "Куриный суп с лапшой": "обед",
         "Шоколадный мусс": "десерт", "Яблочный пирог": "десерт", "Хумус с овощами": "перекус", "Куриная грудка с гречкой": "ужин",
         "Паста с томатами": "ужин"}
got = {t: book.meta(t, recipe(t), "classic")["meal"] for t in cases}
check("раздел определяется по названию", got == cases, str({t: (got[t], cases[t]) for t in cases if got[t] != cases[t]}))
m = book.meta("Салат", recipe("Салат", 15, 180, 30, "Огурцы — 200 г\n- Помидоры — 150 г"), "classic")
check("хештеги: раздел, быстро (≤30 мин), лёгкое (≤350), белок (≥25 г), без мяса", {"#обед", "#быстро", "#лёгкое", "#белок", "#безмяса"} <= set(m["tags"]), str(m))
m = book.meta("Плов", recipe("Плов", 90, 700, 20), "classic")
check("долгое, сытное и с мясом: нет «быстро» и «без мяса», есть «сытное»", "#быстро" not in m["tags"] and "#безмяса" not in m["tags"] and "#сытное" in m["tags"], str(m))
check("время «1 ч 20 мин» = 80 минут, диапазон берёт верхнюю границу", book._minutes("1 ч 20 мин") == 80 and book._minutes("45–60 минут") == 60 and book._minutes("Не важно") is None)

# ---------- допуск в книгу ----------
good = recipe("Гречка с курицей")
check("целый рецепт допускается", book.valid("Гречка с курицей", good))
bad_cases = {"сообщение об ошибке": "😔 Не получилось. " + good, "предупреждение ИИ": "⚠️ Проверьте состав\n\n" + good, "со ссылкой": good + "\nhttp://evil.example",
             "с упоминанием": good + "\nпиши @someone", "слишком короткий": "Рецепт\nккал продукты", "без калорий": good.replace("ккал", "штук"),
             "без состава": good.replace("Продукты", "Прочее")}
for name, t in bad_cases.items():
    check(f"в книгу не попадает рецепт: {name}", not book.valid("Гречка с курицей", t))
check("пустое название не допускается", not book.valid("", good))
id1 = book.add(DATA, "Гречка с курицей", good, ["курица", "гречка"])
check("рецепт добавлен", id1 and book.total() == 1 and book.added_today() == 1)
check("такой же рецепт повторно не добавляется", book.add(DATA, "Гречка с курицей", good, []) is None and book.total() == 1)
check("то же блюдо на другое число порций — отдельный рецепт", book.add({**DATA, "servings": 4}, "Гречка с курицей", recipe("Гречка с курицей", servings=4), []) is not None)
check("мусор не попадает и не роняет", book.add(DATA, "Что-то", "😔 нет") is None and book.add(DATA, "", "") is None)
rec = book.get(id1)
check("в записи: состав покупок, раздел, теги, КБЖУ", rec["terms"] == ["курица", "гречка"] and rec["meal"] == "ужин" and rec["kcal"] == 350 and "#ужин" in rec["tags"], str(rec)[:200])

book.add(DATA, "Омлет с сыром", recipe("Омлет с сыром", 10, 300, 20, "Яйца — 3 шт\n- Сыр — 40 г"), ["яйца", "сыр"])
book.add(DATA, "Суп овощной", recipe("Суп овощной", 40, 150, 6, "Картофель — 300 г\n- Морковь — 100 г"), ["картофель"])
book.add({**DATA, "cuisine": "balanced"}, "Салат с тунцом", recipe("Салат с тунцом", 15, 280, 30, "Тунец — 100 г\n- Огурцы — 100 г"), ["тунец"])
book.add({**DATA, "appliance": ["oven"]}, "Запечённая курица", recipe("Запечённая курица", 25, 420, 40, "Курица — 600 г"), ["курица"])
check("в книге несколько разделов", book.counts_by_meal()["завтрак"] == 1 and book.counts_by_meal()["обед"] >= 2 and book.counts_by_meal()["ужин"] >= 2)

# ---------- подбор из книги ----------
U = 780001
def ids(rows):
    return {r["title"] for r in rows}
hit = book.find(U, DATA)
check("подбор: по умолчанию подходит рецепт под кухню, порции и технику", hit is not None and hit["appliance"] in ("stove", ""), str(hit and hit["title"]))
for i in range(40):
    book.find(U + 1000 + i, DATA)
check("рецепт для духовки не показывают тому, у кого только плита", all(book.find(U + 2000 + i, DATA)["title"] != "Запечённая курица" for i in range(20)))
check("с духовкой и плитой рецепт для духовки доступен", any(book.find(U + 3000 + i, {**DATA, "appliance": ["stove", "oven"]})["title"] == "Запечённая курица" for i in range(60)))
check("другая кухня подбирает свои рецепты", book.find(U + 5, {**DATA, "cuisine": "balanced"})["title"] == "Салат с тунцом")
check("другое число порций: нет в книге — None (составит ИИ)", book.find(U + 6, {**DATA, "servings": 3}) is None)
check("другая порция (large): нет в книге", book.find(U + 7, {**DATA, "portion": "large"}) is None)
check("время 15 мин: только рецепты не дольше", all(book.find(U + 100 + i, {**DATA, "time": "15 мин"})["minutes"] <= 15 for i in range(10)))
check("время 5 мин: ничего подходящего", book.find(U + 8, {**DATA, "time": "5 мин"}) is None)
check("«Не важно»: ограничения по времени нет", book.find(U + 9, {**DATA, "time": "Не важно"}) is not None)
check("пожелание «омлет»: находится омлет", book.find(U + 10, {**DATA, "preferred": "омлет"})["title"] == "Омлет с сыром")
check("пожелание «яйца, сыр»: по продуктам", book.find(U + 11, {**DATA, "preferred": "яйца, сыр"})["title"] == "Омлет с сыром")
check("пожелание «креветки»: в книге нет — None", book.find(U + 12, {**DATA, "preferred": "креветки"}) is None)
check("исключить «яйца»: омлет не подходит", all(book.find(U + 200 + i, {**DATA, "excluded": "яйца"}) is None or book.find(U + 300 + i, {**DATA, "excluded": "яйца"})["title"] != "Омлет с сыром" for i in range(15)))
check("исключить «курица, яйца, картофель»: ничего из этих не показывается",
      all((r := book.find(U + 400 + i, {**DATA, "excluded": "курица, яйца, картофель", "time": "Не важно"})) is None or r["title"] not in ("Гречка с курицей", "Омлет с сыром", "Суп овощной") for i in range(20)))
check("цель 300 ккал: подходят 300 ±10%", all((r := book.find(U + 500 + i, {**DATA, "macro_goal": "300 ккал"})) is None or 270 <= r["kcal"] <= 330 for i in range(15)))
check("цель, которую нельзя разобрать: ИИ", book.find(U + 13, {**DATA, "macro_goal": "хочу похудеть"}) is None)
check("цель 150 ккал: суп овощной", book.find(U + 14, {**DATA, "macro_goal": "150 ккал", "time": "Не важно"})["title"] == "Суп овощной")

# ---------- показываем один раз ----------
V = 795000
d2 = {**DATA, "preferred": "омлет"}
first = book.find(V, d2)
second = book.find(V, d2)
check("тому же человеку тот же рецепт повторно не показывается", first and second is None)
check("другому человеку этот рецепт доступен", book.find(V + 1, d2) is not None)
accounts._q("UPDATE book_seen SET ts=? WHERE uid=?", (time.time() - 15 * 86400, str(V)))
check("через две недели рецепт можно показать снова", book.find(V, d2) is not None)
check("«Удалить мои данные» стирает и журнал показов", accounts.delete_my_data(V)["book_seen"] >= 1 and book.find(V, d2) is not None)

# ---------- просмотр ----------
rows, n = book.search(meal="обед")
check("просмотр по разделу", n >= 2 and all(r["meal"] == "обед" for r in rows))
rows, n = book.search(tag="#быстро")
check("просмотр по хештегу", n >= 2 and all("#быстро" in r["tags"] for r in rows))
check("хештег подбирается точно: «#быстр» не равен «#быстро»", book.search(tag="#быстр")[1] == 0)
rows, n = book.search(q="гречка")
check("поиск по слову из состава", n >= 1 and any("Гречка" in r["title"] for r in rows))
check("поиск со склонением: «курицей» находит «курица»", book.search(q="курицей")[1] >= 1)
check("поиск по времени и калориям", all(r["minutes"] <= 15 for r in book.search(max_min=15)[0]) and all(r["kcal"] <= 200 for r in book.search(max_kcal=200)[0]))
check("инъекция в запросе не ломает поиск", book.search(q="'; DROP TABLE book; --")[1] == 0 and book.total() >= 5 and book.search(tag="x' OR '1'='1")[1] == 0)
check("страницы: смещение и лимит", len(book.search(limit=2)[0]) == 2 and book.search(limit=2, offset=2)[0][0]["id"] != book.search(limit=2)[0][0]["id"])
tags = dict(book.top_tags())
check("популярные хештеги посчитаны", "#ужин" in tags and "#быстро" in tags)

# ---------- бот ----------
gen = {"n": 0}


async def fake_search(data):
    return "q", []


async def fake_gen(data, q, r):
    gen["n"] += 1
    return recipe("Тыквенный суп-пюре", 30, 220, 8, "Тыква — 400 г\n- Сливки — 50 мл")


botmod.search_recipes, botmod.generate_recipe = fake_search, fake_gen


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
            return Message(message_id=9100 + self.n, date=dt.now(), chat=Chat(id=1, type="private"), text=getattr(method, "text", "") or "x").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


seq = [0]


async def ask(uid, data):
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    st = botmod.dp.fsm.get_context(b, uid, uid)
    await st.set_state(RecipeForm.confirm)
    await st.set_data(dict(data))
    seq[0] += 1
    m = Message(message_id=500, date=dt.now(), chat=Chat(id=uid, type="private"), text="x")
    await botmod.dp.feed_update(b, Update(update_id=seq[0], callback_query=CallbackQuery(
        id=str(seq[0]), from_user=User(id=uid, is_bot=False, first_name="T"), chat_instance="x", message=m, data="confirm:go")))
    texts = [p.get("text", "") for n, p in s.calls if n in ("SendMessage", "EditMessageText")]
    return texts


async def bot_part():
    total0 = book.total()
    d = {**DATA, "preferred": "тыква", "time": "30 мин"}
    t1 = await ask(791001, d)
    check("бот: в книге нет тыквенного супа — составляет ИИ", gen["n"] == 1 and any("Тыквенный суп" in x for x in t1), str(t1)[:200])
    check("новый рецепт пополнил книгу", book.total() == total0 + 1 and book.search(q="тыква")[1] == 1)
    t2 = await ask(791002, d)
    check("бот: другому человеку тот же запрос берётся из книги, ИИ не вызывается", gen["n"] == 1 and any("Тыквенный суп" in x for x in t2) and any("Нашёл в книге" in x for x in t2), str(t2)[:300])
    t3 = await ask(791002, d)
    check("бот: тому же человеку повторно — книга уже показывала, составляется новый", gen["n"] == 2, str(gen))
    check("дубль не раздувает книгу", book.total() == total0 + 1)
    t4 = await ask(791003, {**d, "excluded": "тыква"})
    check("бот: с исключением «тыква» книга не отдаёт тыквенный суп", gen["n"] == 3 and book.total() == total0 + 1, str(gen))

asyncio.run(bot_part())

# ---------- сайт ----------
web_api.search_recipes, web_api.generate_recipe = fake_search, fake_gen
cl = TestClient(web_api.app)


def web_recipe(params):
    dev = cl.post("/api/device").json()["device"]
    H = {"X-Device": str(dev)}
    r = cl.post("/api/recipe", json=params, headers=H)
    if r.status_code != 200:
        return r.status_code, None
    job = r.json()["job"]
    for _ in range(60):
        j = cl.get(f"/api/job/{job}", headers=H).json()
        if j.get("status") in ("done", "error"):
            return 200, j
        time.sleep(0.1)
    return 200, None


P = {"cuisine": "classic", "preferred": "кабачок", "time": "30 мин", "servings": 2, "portion": "normal", "appliance": ["stove"]}
g0, n0 = gen["n"], book.total()


def gen_kab(data, q, r):
    gen["n"] += 1
    return recipe("Кабачки с чесноком", 20, 160, 5, "Кабачок — 300 г\n- Чеснок — 2 зубчика")


async def fake_gen2(data, q, r):
    return gen_kab(data, q, r)


web_api.generate_recipe = fake_gen2
code, j = web_recipe(P)
check("сайт: нового рецепта нет в книге — составляет ИИ и пополняет книгу", code == 200 and j and j.get("status") == "done" and gen["n"] == g0 + 1 and book.total() == n0 + 1, str(j)[:200])
code, j = web_recipe(P)
check("сайт: другое устройство получает его из книги без ИИ", code == 200 and j and j.get("status") == "done" and gen["n"] == g0 + 1 and "Кабачки с чесноком" in str(j), str(j)[:200])


# ---------- сайт: книга ----------
dev = cl.post("/api/device").json()["device"]
H = {"X-Device": str(dev)}
r = cl.get("/api/book", headers=H).json()
check("сайт: /api/book отдаёт страницу, всего, разделы со счётчиками и хештеги", len(r["items"]) <= 8 and r["total"] == book.total() and len(r["meta"]["meals"]) == 5 and r["meta"]["tags"] and r["meta"]["mains"], str(r)[:200])
it = r["items"][0]
check("сайт: у рецепта текст, теги, калории и ссылка на карточку", it["text"] and it["card"].startswith("/api/bookimg/") and it["tags"], str(it)[:200])
png = cl.get(it["card"])
check("сайт: карточка рецепта это PNG, кэшируется", png.status_code == 200 and png.content[:4] == b"\x89PNG" and "max-age" in png.headers.get("cache-control", ""))
check("сайт: нет такого рецепта — 404, мусорный номер не падает", cl.get("/api/bookimg/99999999.png").status_code == 404 and cl.get("/api/bookimg/abc.png").status_code == 422 and cl.get("/api/bookimg/0.png").status_code == 404)
check("сайт: раздел «десерт»", all(i["meal"] == "десерт" for i in cl.get("/api/book?meal=десерт", headers=H).json()["items"]))
check("сайт: слово и основа", cl.get("/api/book?q=гречка", headers=H).json()["total"] >= 1 and cl.get("/api/book?main=курица", headers=H).json()["total"] >= 1)
check("сайт: техника и порции", all("Запечённая" in i["title"] or True for i in cl.get("/api/book?appliance=oven", headers=H).json()["items"]) and cl.get("/api/book?appliance=oven", headers=H).json()["total"] == 1 and cl.get("/api/book?servings=4", headers=H).json()["total"] == 1)
check("сайт: калории и время", all(i["kcal"] <= 200 for i in cl.get("/api/book?max_kcal=200", headers=H).json()["items"]) and all(i["minutes"] <= 15 for i in cl.get("/api/book?max_min=15", headers=H).json()["items"]))
check("сайт: хештег", cl.get("/api/book?tag=%23быстро", headers=H).json()["total"] >= 1 and cl.get("/api/book?tag=%23быстр", headers=H).json()["total"] == 0)
check("сайт: страница за пределом пуста, отрицательная и огромная не падают", cl.get("/api/book?page=999", headers=H).json()["items"] == [] and cl.get("/api/book?page=-5", headers=H).status_code == 200 and cl.get("/api/book?page=99999999999", headers=H).status_code in (200, 422))
check("сайт: мусор и инъекция в параметрах не ломают", cl.get("/api/book?q=%27;DROP%20TABLE%20book;--&meal=zzz&main=zzz&appliance=zzz&max_min=-1&servings=99", headers=H).status_code == 200 and book.total() > 5)
check("сайт: книга требует устройство", cl.get("/api/book").status_code in (401, 422))
code, j = web_recipe({**P, "preferred": "омлет", "time": "Не важно"})
check("сайт: рецепт приходит с карточкой, если он в книге", code == 200 and j and "/api/bookimg/" in str(j), str(j)[:300])
html = open("web/index.html", encoding="utf-8").read()
check("сайт: рядом с «Любимыми» кнопка «Книга», вкладка и разделы в странице", "data-open-book" in html and 'class="favrow"' in html and "viewBook" in html and "book:viewBook" in html.replace(" ", ""))

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
