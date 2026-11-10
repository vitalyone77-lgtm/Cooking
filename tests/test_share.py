"""Поделиться рецептом: ссылка из бота и сайта, страница с продуктами, магазинами, шагами и таймерами, пересчёт порций, картинка превью."""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime as dt

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_sh.db", "accounts_sh.db", "accounts_sh.db-wal", "accounts_sh.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_sh.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_sh.db"
os.environ["WEB_DATA_DIR"] = "web_data_sh"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from fastapi.testclient import TestClient

import tg_safety
tg_safety.FloodGuard.__init__.__defaults__ = (100000, 10)
import accounts
import bot as botmod
import recipe_share as rs
import web_api

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


R = ("🍽 Куриный суп\n\n⏱ Время: 40 мин | 👥 Порций: 2 | 🔥 Способ: Плита\n\n*Пищевая ценность (на 1 порцию):*\nКалорийность: 350 ккал | Б: 30 г | Ж: 9 г | У: 30 г\n\n"
     "*Продукты:*\n- Курица (филе) — 300 г\n- Рис — 100 г\n- Соль, перец — по вкусу\n\n*Инструкция:*\n1. Сварите курицу 25–30 минут.\n2. Добавьте рис, варите 15 мин.\n3. Посолите.\n\n💡 Совет: зелень добавьте в конце.")
d = rs.parse(R)
check("разбор: название, сведения, продукты, шаги, совет, порции", d["title"] == "Куриный суп" and len(d["products"]) == 3 and len(d["steps"]) == 3 and d["tips"] and d["servings"] == 2
      and any("Калорийность" in i for i in d["info"]), json.dumps(d, ensure_ascii=False)[:400])
check("продукт: название и количество отдельно", d["products"][0]["name"] == "Курица (филе)" and d["products"][0]["amount"] == "300 г")
check("поиск в магазине по чистому названию", all("Курица" in u or "%D0%9A%D1%83%D1%80%D0%B8%D1%86%D0%B0" in u for u in d["products"][0]["links"].values()) and "%28" not in list(d["products"][0]["links"].values())[0])
check("«Соль, перец» — одна строка, поиск по первому", d["products"][2]["name"] == "Соль, перец" and "%2C" not in list(d["products"][2]["links"].values())[0])
check("таймеры у шагов", d["steps"][0]["timers"] == [[25, 30]] or d["steps"][0]["timers"] == [(25, 30)], str(d["steps"][0]))
check("шаг без времени без таймера", not d["steps"][2]["timers"])
try:
    rs.create(1, "", "просто текст без рецепта, тут нет ни продуктов, ни шагов")
    bad = True
except ValueError:
    bad = False
check("не рецепт: понятный отказ", not bad)
rid = rs.create(1, "", R)
check("тот же рецепт — та же ссылка", rs.create(1, "", R) == rid)

C = 798001
user, chat = User(id=C, is_bot=False, first_name="T"), Chat(id=C, type="private")


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append((name, method.model_dump(exclude_none=True)))
        if name in ("SendMessage",):
            return Message(message_id=9900, date=dt.now(), chat=chat, text="x").as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


async def main():
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    kb = botmod.recipe_kb(R, C)
    check("бот: «Поделиться» под рецептом в ряд с PDF", any([x.callback_data for x in row] == ["fav:save", "pdf:r", "rs:mk"] or ("pdf:r" in [x.callback_data for x in row] and "rs:mk" in [x.callback_data for x in row]) for row in kb.inline_keyboard), str([[x.callback_data for x in r] for r in kb.inline_keyboard]))
    plain = R.replace("*", "")
    m = Message(message_id=700, date=dt.now(), chat=chat, text=plain, reply_markup=kb)
    await botmod.dp.feed_update(b, Update(update_id=1, callback_query=CallbackQuery(id="1", from_user=user, chat_instance="x", message=m, data="rs:mk")))
    p = next((d for n, d in reversed(s.calls) if n == "SendMessage"), {})
    check("бот: ссылка на рецепт и кнопки открыть/переслать", "/rc/" in p.get("text", "") and "t.me/share" in str(p.get("reply_markup")), str(p)[:300])
    rid2 = p.get("text", "").split("/rc/")[1].split()[0] if "/rc/" in p.get("text", "") else ""
    check("бот: страница рецепта создана", rs.get(rid2) and rs.get(rid2)["title"] == "Куриный суп")


asyncio.run(main())

cl = TestClient(web_api.app)
dev = cl.post("/api/device").json()["device"]
r = cl.post("/api/share-recipe", json={"title": "Куриный суп", "text": R}, headers={"X-Device": dev})
check("сайт: ссылка на рецепт", r.status_code == 200 and "/rc/" in r.json()["url"], r.text)
rid3 = r.json()["url"].split("/rc/")[1]
pg = cl.get(f"/rc/{rid3}")
check("страница открывается без входа, не индексируется", pg.status_code == 200 and "noindex" in pg.text and "Куриный суп" in pg.text and pg.headers.get("x-robots-tag") == "noindex")
check("страница: превью-картинка и ссылка на помощника", f"/rc/{rid3}.png" in pg.text and "utm_source=recipe" in pg.text and "Открыть помощника" in pg.text)
img = cl.get(f"/rc/{rid3}.png")
check("картинка превью рисуется", img.status_code == 200 and img.content[:4] == b"\x89PNG")
dj = cl.get(f"/rc/{rid3}/data?to=4").json()
check("пересчёт порций на странице", dj["servings"] == 4 and dj["products"][0]["amount"] == "600 г", str(dj)[:300])
check("чужой номер: не найдено", cl.get("/rc/zzzzzzzzzzzz").status_code == 404 and cl.get("/rc/zzzzzzzzzzzz/data").status_code == 404)
idx = open("web/index.html", encoding="utf-8").read()
check("сайт: кнопка «Поделиться рецептом»", "data-share-r" in idx and "/api/share-recipe" in idx)
node = shutil.which("node")
if node:
    js = re.search(r"<script>(.*)</script>", pg.text, re.S).group(1)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
    r = subprocess.run([node, "--check", f.name], capture_output=True, text=True)
    os.unlink(f.name)
    check("скрипт страницы без синтаксических ошибок", r.returncode == 0, r.stderr[:300])


check("ссылка живёт 30 дней", rs.TTL == 30 * 86400)
pg2 = cl.get(f"/rc/{rid3}").text
check("страница: сохранить в избранное на сайте и в боте", f"/?rc={rid3}" in pg2 and f"start=rc_{rid3}" in pg2 and "30 дней" in pg2)
dev2 = cl.post("/api/device").json()["device"]
r = cl.post("/api/favorites/from-share", json={"id": rid3}, headers={"X-Device": dev2})
favs = cl.get("/api/favorites", headers={"X-Device": dev2}).json()["items"]
check("сайт: рецепт по ссылке в «Любимых»", r.status_code == 200 and any(f["title"] == "Куриный суп" for f in favs), r.text)
cl.post("/api/favorites/from-share", json={"id": rid3}, headers={"X-Device": dev2})
check("повторное сохранение не дублирует", len(cl.get("/api/favorites", headers={"X-Device": dev2}).json()["items"]) == len(favs))
check("устаревшая ссылка: понятный текст", cl.post("/api/favorites/from-share", json={"id": "zzzzzzzzzzzz"}, headers={"X-Device": dev2}).status_code == 404)
accounts._q("UPDATE shared_recipes SET ts=? WHERE id=?", (time.time() - 31 * 86400, rid3))
check("через 30 дней страница не открывается", cl.get(f"/rc/{rid3}").status_code == 404)
idx2 = open("web/index.html", encoding="utf-8").read()
check("сайт: открытие с ?rc= сохраняет и показывает «Любимые»", "/api/favorites/from-share" in idx2 and "|rc)=" in idx2)


async def bot_start():
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    rid4 = rs.create(5, "", R)
    await botmod.dp.feed_update(b, Update(update_id=50, message=Message(message_id=60, date=dt.now(), chat=chat, from_user=user, text=f"/start rc_{rid4}")))
    texts = [d.get("text", "") for n, d in s.calls if n == "SendMessage"]
    import favorites
    check("бот: /start rc_ сохраняет в избранное и присылает рецепт", any("сохранён в избранное" in t for t in texts) and any("Куриный суп" in t and "Продукты" in t for t in texts)
          and any(f["title"] == "Куриный суп" for f in favorites.get_favorites(C)), str(texts)[:300])
    await botmod.dp.feed_update(b, Update(update_id=51, message=Message(message_id=61, date=dt.now(), chat=chat, from_user=user, text="/start rc_zzzzzzzzzzzz")))
    check("бот: устаревшая ссылка", "устарела" in [d.get("text", "") for n, d in s.calls if n == "SendMessage"][-1])


asyncio.run(bot_start())

print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
