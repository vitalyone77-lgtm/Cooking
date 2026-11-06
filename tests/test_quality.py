"""Проверка доработок: страховка запросов к Telegram, атомарная запись, SEO-страницы и маршруты сайта."""
import asyncio
import json
import os
import sys
from datetime import datetime

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
os.environ["WEB_DATA_DIR"] = "/tmp/ct/web_data_test"

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# ---------- 1. tg_safety ----------
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage, EditMessageText
from aiogram.types import Message, Chat
from tg_safety import SafeSendMiddleware, split_text


class FakeSession(BaseSession):
    def __init__(self, mode):
        super().__init__()
        self.mode, self.calls = mode, []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        pm = method.parse_mode
        self.calls.append((type(method).__name__, getattr(method, "text", None), pm,
                           getattr(method, "reply_markup", None)))
        if self.mode == "markdown" and pm is not None:
            raise TelegramBadRequest(method=method, message="Bad Request: can't parse entities: x")
        if self.mode == "notmod":
            raise TelegramBadRequest(method=method, message="Bad Request: message is not modified")
        return Message(message_id=1, date=datetime.now(), chat=Chat(id=1, type="private"), text="ok")

    async def stream_content(self, *a, **k):
        raise NotImplementedError


async def tg_tests():
    s = FakeSession("markdown")
    s.middleware(SafeSendMiddleware())
    b = Bot("123:ABC", session=s, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    await b.send_message(1, "текст с _битой разметкой")
    check("битая разметка: повтор без parse_mode", len(s.calls) == 2 and s.calls[1][2] is None, str(s.calls))

    s2 = FakeSession("ok")
    s2.middleware(SafeSendMiddleware())
    b2 = Bot("123:ABC", session=s2, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder().button(text="x", callback_data="y").as_markup()
    long = "\n".join(f"строка {i} " + "а" * 80 for i in range(120))  # ~10 000 символов
    await b2.send_message(1, long, reply_markup=kb)
    sizes = [len(c[1]) for c in s2.calls]
    check("длинный текст разбит на части ≤4096", len(s2.calls) >= 3 and max(sizes) <= 4096, str(sizes))
    check("кнопки только у последней части", s2.calls[-1][3] is not None and all(c[3] is None for c in s2.calls[:-1]))
    check("текст не потерян при разбиении", "".join(c[1] for c in s2.calls).replace("\n", "") == long.replace("\n", ""))

    s3 = FakeSession("notmod")
    s3.middleware(SafeSendMiddleware())
    b3 = Bot("123:ABC", session=s3)
    r = await b3.edit_message_text("x", chat_id=1, message_id=1)
    check("«message is not modified» не роняет обработчик", r is True)

    check("split_text: одна огромная строка режется", max(len(p) for p in split_text("я" * 9000)) <= 3900)


asyncio.run(tg_tests())

# ---------- 2. атомарная запись ----------
from jsonio import write_json_atomic
p = "/tmp/ct/_atomic_test.json"
write_json_atomic(p, {"a": "привет"})
check("атомарная запись JSON", json.load(open(p, encoding="utf-8")) == {"a": "привет"})
check("временные файлы не остаются", not [f for f in os.listdir("/tmp/ct") if f.startswith("_atomic_test.json.")])

# ---------- 3. сайт ----------
from fastapi.testclient import TestClient
import config
import web_api

c = TestClient(web_api.app)
r = c.get("/pitanie/menu-na-den")
check("SEO-страница открывается", r.status_code == 200 and "<h1>Меню на день с калориями и КБЖУ</h1>" in r.text)
check("у страницы canonical и og:image 1200x630", 'rel="canonical" href="https://menu.ymayaka.ru/pitanie/menu-na-den"' in r.text
      and "og-image.png" in r.text, r.text[:400])
check("JSON-LD FAQ и хлебные крошки", '"FAQPage"' in r.text and '"BreadcrumbList"' in r.text)
check("несуществующая статья — 404", c.get("/pitanie/net-takoy").status_code == 404)
check("список статей", c.get("/pitanie").status_code == 200)
sm = c.get("/sitemap.xml").text
check("sitemap содержит все статьи", sm.count("<loc>") == 1 + 1 + len(__import__("seo_pages").PAGES) and "<lastmod>" in sm, sm[:300])
check("llms.txt со статьями", "/pitanie/plan-pitaniya-na-3-dnya" in c.get("/llms.txt").text)
idx = c.get("/", headers={"Accept-Encoding": "gzip"})
check("главная сжимается gzip", idx.headers.get("content-encoding") == "gzip", str(idx.headers))
check("заглушка VERIFY не видна", "<!--VERIFY-->" not in idx.text)
config.YANDEX_VERIFICATION = "abc123"
check("код Яндекса подставляется", 'name="yandex-verification" content="abc123"' in c.get("/").text)
config.YANDEX_VERIFICATION = ""
check("ссылки на статьи на главной", "/pitanie/menu-na-den" in idx.text)
check("API помечен noindex", c.get("/api/meta").headers.get("x-robots-tag", "").startswith("noindex"))

# админка: без WEB_SECRET вход закрыт; кривой JSON — 400, а не 500
old = config.WEB_SECRET
config.WEB_SECRET = ""
check("без WEB_SECRET админка закрыта", c.post("/admin/login", json={"password": config.ADMIN_PASSWORD or "x"}).status_code == 401)
config.WEB_SECRET = old
r = c.post("/admin/login", content=b"{bad json", headers={"Content-Type": "application/json"})
check("кривой JSON при входе — 400", r.status_code == 400, str(r.status_code))
for _ in range(1200):
    web_api._new_dev_log[f"1.2.3.{_}"] = [0.0]
web_api._prune(web_api._new_dev_log, 3600)
check("старые записи по IP чистятся", len(web_api._new_dev_log) == 0)

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
sys.exit(1 if fails else 0)
