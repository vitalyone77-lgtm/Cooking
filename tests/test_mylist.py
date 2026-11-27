"""Свой список покупок (подписка): разбор текста, бот (кнопка в профиле, ввод, список с галочками, магазины), сайт,
страница списка: «Найти в магазине» с выбором магазина и маленькая «Поделиться»."""
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
for f in ("usage_ml.db", "accounts_ml.db", "accounts_ml.db-wal", "accounts_ml.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_ml.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_ml.db"
os.environ["WEB_DATA_DIR"] = "web_data_ml"

from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from fastapi.testclient import TestClient

import tg_safety
tg_safety.FloodGuard.__init__.__defaults__ = (100000, 10)
import accounts
import bot as botmod
import bot_profile
import config
import plans
import shoplists
import web_api

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


items = shoplists.parse_manual("молоко 2 л, хлеб, яйца 10 шт; бумажные полотенца\n1) сыр — 200 г\n- зелень\nкефир 1,5 л\nхлеб")
check("разбор: количество, нумерация, тире, повтор убран", [(i["name"], i["amount"]) for i in items] ==
      [("Молоко", "2 л"), ("Хлеб", ""), ("Яйца", "10 шт"), ("Бумажные полотенца", ""), ("Сыр", "200 г"), ("Зелень", ""), ("Кефир", "1,5 л")], str(items))
check("разбор: «2 л молока» не теряет начало", shoplists.parse_manual("2 л молока")[0]["name"] == "2 л молока")

config.PAYWALL_ENABLED = True
config.WEB_SECRET = "secret-ml-test"                    # подпись «владелец» для страницы /l/<id>
accounts.settings_set({"PREMIUM_FEATURES": ["memory", "pdf", "cart", "noads", "tailor", "mylist"]}, "test")
plans.reload()
FREE, PRO = 792001, 792002
accounts.grant_days(PRO, 30, "promo:T")


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        d = method.model_dump(exclude_none=True)
        self.calls.append((name, d))
        if name in ("SendMessage", "EditMessageText"):
            return Message(message_id=9900, date=dt.now(), chat=Chat(id=d.get("chat_id") or 1, type="private"), text=d.get("text", "x")).as_(bot)
        return True

    async def stream_content(self, *a, **k):
        raise NotImplementedError


seq = [0]


def upd(uid, data=None, text=None):
    seq[0] += 1
    u, c = User(id=uid, is_bot=False, first_name="T"), Chat(id=uid, type="private")
    if data:
        m = Message(message_id=100 + seq[0], date=dt.now(), chat=c, text="x")
        return Update(update_id=seq[0], callback_query=CallbackQuery(id=str(seq[0]), from_user=u, chat_instance="x", message=m, data=data))
    return Update(update_id=seq[0], message=Message(message_id=100 + seq[0], date=dt.now(), chat=c, from_user=u, text=text))


def sent(s):
    return [d for n, d in s.calls if n == "SendMessage"]


async def main():
    s = FakeSession()
    b = Bot(token="42:TEST", session=s)
    accounts.consent_set(PRO)
    accounts.goals_set(PRO, feats=["diary"])
    t, kb, _ = bot_profile._profile_main(PRO, 30)
    rows = [[x.callback_data for x in r] for r in kb.inline_keyboard]
    check("профиль: «Свой список» рядом с «Составом», ниже — Настройки и Справка", ["ml:o", "lb:o"] in rows and rows[-1] == ["pf:set", "help:open"], str(rows))
    t, kb = bot_profile._profile_view(PRO) if hasattr(bot_profile, "_profile_view") else (None, None)

    await botmod.dp.feed_update(b, upd(FREE, "ml:o"))
    check("без подписки: объяснение и подписка", any("в подписке" in d.get("text", "") for d in sent(s)))
    s.calls.clear()
    await botmod.dp.feed_update(b, upd(PRO, "ml:o"))
    check("с подпиской: просит продукты", any("Что купить" in d.get("text", "") for d in sent(s)))
    await botmod.dp.feed_update(b, upd(PRO, text="молоко 2 л, хлеб, яйца 10 шт"))
    last = sent(s)[-1]
    kbs = str(last.get("reply_markup"))
    check("пришёл список", "Молоко — 2 л" in last["text"] and "Яйца — 10 шт" in last["text"], last["text"][:200])
    check("под списком: открыть с галочками и маленькое 📤 в одной строке, магазины отдельно", "/l/" in kbs and "t.me/share" in kbs and "ml:sh:" in kbs)
    lid = re.search(r"ml:sh:([A-Za-z0-9_-]+)", kbs).group(1)
    s.calls.clear()
    await botmod.dp.feed_update(b, upd(PRO, f"ml:sh:{lid}"))
    check("«Найти в магазине»: ссылки на поиск продуктов", any("Молоко" in d.get("text", "") and "http" in d.get("text", "") for d in sent(s)), str(sent(s))[:300])
    s.calls.clear()
    await botmod.dp.feed_update(b, upd(PRO, text="хлеб"))
    check("после списка анкета закрыта (обычный текст — не новый список)", not any("📝 Список покупок" in d.get("text", "") for d in sent(s)))


asyncio.run(main())

cl = TestClient(web_api.app)


def dev_for(uid):
    d = cl.post("/api/device").json()["device"]
    accounts._q("INSERT INTO links(dev_id, tg_id, ts) VALUES(?,?,?)", (web_api.web_db.device_id(d), uid, time.time()))
    return {"X-Device": d}


Hf, Hp = dev_for(FREE), dev_for(PRO)
check("сайт: без подписки — 403", cl.post("/api/mylist", json={"text": "хлеб"}, headers=Hf).status_code == 403)
check("сайт: замок для бесплатного, открыто для подписки", cl.get("/api/me", headers=Hf).json()["feat"].get("mylist") is False
      and cl.get("/api/me", headers=Hp).json()["feat"].get("mylist") is True)
r = cl.post("/api/mylist", json={"text": "молоко 2 л\nхлеб", "title": "Домой"}, headers=Hp)
check("сайт: список создан", r.status_code == 200 and r.json()["title"] == "Домой" and len(r.json()["items"]) == 2, r.text[:200])
pg = cl.get("/l/" + r.json()["id"]).text
check("страница: «Найти в магазине» и маленькая «Поделиться» рядом", 'id="find"' in pg and 'class="sq"' in pg and "Пятёрочка" in pg)

# ---- кнопки «⭐ Избранное» и «📋 Последний список» ----
saved = cl.get("/api/mylist/saved", headers=Hp)
check("сайт: список с названием попал в избранное",
      saved.status_code == 200 and [x["id"] for x in saved.json()] == [r.json()["id"]], saved.text[:200])
bot_row = accounts._q("SELECT id, saved FROM shop_lists WHERE uid=? AND title='Список покупок' ORDER BY ts LIMIT 1",
                      (str(PRO),), one=True)
check("сайт: список из бота без названия в избранное не попал",
      bool(bot_row) and bot_row[1] == 0 and all(x["id"] != bot_row[0] for x in saved.json()), str(bot_row))
r2 = cl.post("/api/mylist", json={"text": "соль\nперец"}, headers=Hp)
check("сайт: безымянный список создан", r2.status_code == 200 and r2.json()["title"] == "Список покупок", r2.text[:200])
lastj = cl.get("/api/mylist/last", headers=Hp).json()
check("сайт: «последний» — самый свежий, включая безымянный",
      lastj.get("id") == r2.json()["id"] and lastj.get("count") == 2, str(lastj))
check("сайт: у «последнего» абсолютная ссылка и число купленного",
      lastj.get("url", "").endswith("/l/" + r2.json()["id"]) and lastj.get("checked") == 0, str(lastj))
check("сайт: избранное и последний закрыты без подписки",
      cl.get("/api/mylist/saved", headers=Hf).status_code == 403
      and cl.get("/api/mylist/last", headers=Hf).status_code == 403)
PRO2 = 792003
accounts.grant_days(PRO2, 30, "promo:T")
Hp2 = dev_for(PRO2)
check("сайт: у другого подписчика свои списки (пусто)",
      cl.get("/api/mylist/saved", headers=Hp2).json() == []
      and cl.get("/api/mylist/last", headers=Hp2).json() == {})
# список из рецепта с автоназванием — разовый, в избранное не идёт
recipe_lid = shoplists.create(PRO, "Покупки на 3 рецепта", [{"name": "рис", "amount": ""}])
check("сайт: список из рецепта (автоназвание) не в избранном",
      all(x["id"] != recipe_lid for x in cl.get("/api/mylist/saved", headers=Hp).json()))
check("сайт: список из рецепта виден как последний",
      cl.get("/api/mylist/last", headers=Hp).json().get("id") == recipe_lid)
accounts._q("UPDATE shop_lists SET ts=? WHERE id=?", (time.time() - shoplists.TTL - 10, recipe_lid))
check("сайт: список старше 30 дней не считается последним",
      cl.get("/api/mylist/last", headers=Hp).json().get("id") == r2.json()["id"])
accounts._q("DELETE FROM shop_lists WHERE id=?", (r2.json()["id"],))
check("сайт: после удаления последнего показывается предыдущий",
      cl.get("/api/mylist/last", headers=Hp).json().get("id") == r.json()["id"])

# ---- этап 2: правка, удаление, «Сохранить в избранное» на странице /l/<id> ----
rd = cl.post("/api/mylist", json={"text": "молоко\nхлеб", "title": "Базовый"}, headers=Hp)
lid = rd.json()["id"]
one = cl.get("/api/mylist/" + lid, headers=Hp)
check("этап 2: владелец получает список для правки",
      one.status_code == 200 and one.json()["saved"] is True and one.json()["title"] == "Базовый", one.text[:200])
check("этап 2: чужой список не отдаётся", cl.get("/api/mylist/" + lid, headers=Hp2).status_code == 404)
check("этап 2: чужой не может редактировать",
      cl.put("/api/mylist/" + lid, json={"title": "X", "text": "соль"}, headers=Hp2).status_code == 403)
ru = cl.put("/api/mylist/" + lid, json={"title": "Новый", "text": "соль\nперец"}, headers=Hp)
check("этап 2: владелец меняет название и продукты",
      ru.status_code == 200 and ru.json()["title"] == "Новый"
      and [i["name"] for i in ru.json()["items"]] == ["Соль", "Перец"], ru.text[:200])
one = cl.get("/api/mylist/" + lid, headers=Hp).json()
check("этап 2: после правки галочки сброшены", one["done"] == [] and one["title"] == "Новый")
check("этап 2: пустые продукты не принимаются",
      cl.put("/api/mylist/" + lid, json={"title": "X", "text": "   "}, headers=Hp).status_code == 400)
check("этап 2: изменённый список в избранном",
      cl.get("/api/mylist/saved", headers=Hp).json()[0]["title"] == "Новый")
check("этап 2: saved переключается туда-обратно",
      cl.post("/api/mylist/" + lid + "/save", headers=Hp).json()["saved"] is False
      and cl.post("/api/mylist/" + lid + "/save", headers=Hp).json()["saved"] is True)
check("этап 2: чужой не может переключить saved",
      cl.post("/api/mylist/" + lid + "/save", headers=Hp2).status_code == 403)
tok = shoplists.owner_token(lid)
check("этап 2: подпись владельца формируется", len(tok) == 32, tok)
pg_owner = cl.get("/l/" + lid + "?k=" + tok).text
pg_guest = cl.get("/l/" + lid).text
check("этап 2: владелец видит «Убрать из избранного»",
      'id="save-btn"' in pg_owner and "Убрать из избранного" in pg_owner)
check("этап 2: по обычной ссылке кнопки нет", 'id="save-btn"' not in pg_guest)
check("этап 2: чужая подпись не подходит",
      cl.post("/api/mylist/" + lid + "/save?k=bad").status_code in (401, 403))
j = cl.post("/api/mylist/" + lid + "/save?k=" + tok)
check("этап 2: со страницы saved переключается по подписи", j.status_code == 200 and j.json()["saved"] is False, j.text[:200])
check("этап 2: после переключения кнопка предлагает сохранить",
      "Сохранить в избранное" in cl.get("/l/" + lid + "?k=" + tok).text)
check("этап 2: чужой не может удалить", cl.delete("/api/mylist/" + lid, headers=Hp2).status_code == 403)
check("этап 2: владелец удаляет", cl.delete("/api/mylist/" + lid, headers=Hp).json()["ok"] is True)
check("этап 2: удалённый список не найден",
      cl.get("/api/mylist/" + lid, headers=Hp).status_code == 404 and cl.get("/l/" + lid).status_code == 404)

node = shutil.which("node")
if node:
    js = re.search(r"<script>(.*)</script>", pg, re.S).group(1)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
    rr = subprocess.run([node, "--check", f.name], capture_output=True, text=True)
    os.unlink(f.name)
    check("скрипт страницы списка без ошибок", rr.returncode == 0, rr.stderr[:300])
idx = open("web/index.html", encoding="utf-8").read()
check("сайт: «Свой список» внизу профиля над Настройками, с замком", 'data-tab-go="mylist"' in idx and 'data-gate="mylist"' in idx and "mylist:viewMyList" in idx)
check("сайт: над «Создать список» две кнопки — избранное и последний",
      'data-ml-fav="1"' in idx and 'data-ml-last="1"' in idx and idx.index('data-ml-fav="1"') < idx.index('data-ml-go="1"'))
check("сайт: подсказка, что название сохраняет список в избранном", "сохранится в «Избранном»" in idx)
check("этап 2: в избранном есть ✏️, форма правки и удаление",
      "data-edit-list" in idx and "data-save-list" in idx and "data-delete-list" in idx)
check("этап 2: владельцу отдаётся ссылка с подписью", "url_owner" in idx)

print("ИТОГО: упало", len(fails))
sys.exit(1 if fails else 0)
