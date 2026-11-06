"""Карточка рецепта, публичные страницы рецептов (/r/…), адреса из названий."""
import io
import os
import sys

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_cp.db", "accounts_cp.db", "accounts_cp.db-wal", "accounts_cp.db-shm", "user_data.json"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_cp.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_cp.db"
os.environ["WEB_DATA_DIR"] = "web_data_cp"

from PIL import Image

import config
config.WEB_APP_URL = "https://menu.test"
import accounts
import cards
import recipe_pages
import social

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


R = ("🍽 Куриный суп с лапшой\n\n⏱ Время: 30 мин | 👥 Порций: 2 | 🔥 Способ: Плита\n\n"
     "Калорийность: 320 ккал | Б: 24 г | Ж: 9 г | У: 36 г\n\n*Продукты:*\n- Куриное филе — 200 г")

# ---------- карточка ----------
d = cards.parse("🍽 Куриный суп с лапшой", R)
check("из рецепта берутся название, время, порции и КБЖУ",
      d == {"title": "Куриный суп с лапшой", "time": "30 мин", "servings": "2", "kcal": "320", "protein": "24", "fat": "9", "carb": "36"}, str(d))
png = cards.make_card("🍽 Куриный суп с лапшой", R)
im = Image.open(io.BytesIO(png))
check("карточка: PNG 1200×630", png[:8] == b"\x89PNG\r\n\x1a\n" and im.size == (1200, 630), str(im.size))
check("карточка не пустая: есть и фон, и тёмный текст, и зелёный акцент", len({im.getpixel((x, 150)) for x in range(80, 600, 5)}) > 2)
long = cards.make_card("🍽 " + "Запечённая куриная грудка с картофелем, брокколи и сливочно-горчичным соусом по-домашнему " * 2, R)
check("очень длинное название не ломает карточку", Image.open(io.BytesIO(long)).size == (1200, 630))
bare = cards.make_card("Каша", "Просто текст без чисел")
check("рецепт без КБЖУ и времени тоже рисуется", Image.open(io.BytesIO(bare)).size == (1200, 630))
check("карточку можно сделать другого размера", Image.open(io.BytesIO(cards.make_card("Суп", R, (1080, 1080)))).size == (1080, 1080))

# ---------- адреса ----------
s1, s2 = social.slugify("Куриный суп с лапшой"), social.slugify("Куриный суп с лапшой")
check("адрес: транслит, дефисы, случайный хвост", s1.startswith("kurinyy-sup-s-lapshoy-") and s1 != s2 and recipe_pages.SLUG_RE.match(s1), s1)
check("адрес: ё, ь, ъ и знаки не ломают", recipe_pages.SLUG_RE.match(social.slugify("Щи «по-русски»: объёмные!")), social.slugify("Щи «по-русски»: объёмные!"))
check("адрес: пустое название не даёт пустой адрес", recipe_pages.SLUG_RE.match(social.slugify("")))
check("адрес страницы строится от адреса сайта", social.page_url("abc-123") == "https://menu.test/r/abc-123")

# ---------- страницы ----------
accounts.pub_recipe_add("kurinyy-sup-a1b2c3", "Куриный суп с лапшой", R, png)
accounts.pub_recipe_add("xss-test-aaaaaa", '<script>alert(1)</script> суп', "Текст <b>жирный</b> & \"кавычки\"", None)
rec = accounts.pub_recipe_get("kurinyy-sup-a1b2c3")
check("страница сохраняется и читается", rec and rec["title"] == "Куриный суп с лапшой" and accounts.pub_recipe_image("kurinyy-sup-a1b2c3") == png)
check("нет страницы — пусто", accounts.pub_recipe_get("net-takoy") is None and accounts.pub_recipe_image("net-takoy") is None)

from fastapi.testclient import TestClient
import web_api
with TestClient(web_api.app) as cl:
    r = cl.get("/r/kurinyy-sup-a1b2c3")
    h = r.text
    check("страница открывается", r.status_code == 200 and "Куриный суп с лапшой" in h and "Калорийность: 320 ккал" in h)
    check("метки для превью: картинка с полным адресом, размер, заголовок, адрес страницы",
          'property="og:image" content="https://menu.test/rimg/kurinyy-sup-a1b2c3.png"' in h and 'og:image:width" content="1200"' in h
          and 'og:image:height" content="630"' in h and 'property="og:title" content="Куриный суп с лапшой"' in h
          and 'rel="canonical" href="https://menu.test/r/kurinyy-sup-a1b2c3"' in h and "summary_large_image" in h, h[:600])
    check("на странице кнопки на сайт и бота и оговорка про КБЖУ", "Открыть сайт" in h and f"t.me/{config.BOT_USERNAME}" in h and "приблизительные" in h)
    img = cl.get("/rimg/kurinyy-sup-a1b2c3.png")
    check("картинка отдаётся как PNG с кэшем", img.status_code == 200 and img.content == png and img.headers["content-type"] == "image/png"
          and "max-age" in img.headers.get("cache-control", ""))
    check("несуществующая страница и картинка — 404", cl.get("/r/net-takoy").status_code == 404 and cl.get("/rimg/net-takoy.png").status_code == 404)
    check("плохой адрес — 404, а не ошибка", cl.get("/r/..%2Fetc").status_code == 404 and cl.get("/r/A_B").status_code == 404)
    x = cl.get("/r/xss-test-aaaaaa").text
    check("название и текст экранируются (нет выполнения чужого кода)",
          "<script>alert(1)</script>" not in x and "&lt;script&gt;" in x and "<b>жирный</b>" not in x and "&lt;b&gt;" in x)
    check("страница без картинки отдаёт 404 на картинку", cl.get("/rimg/xss-test-aaaaaa.png").status_code == 404)

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
