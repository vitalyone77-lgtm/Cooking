"""Рецепты книги находятся поисковиками: страница с schema.org/Recipe, список, карта сайта; скрытые из-за оценок не показываются."""
import json
import os
import re
import sys

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
for f in ("usage_sb.db", "accounts_sb.db", "accounts_sb.db-wal", "accounts_sb.db-shm"):
    if os.path.exists(f):
        os.remove(f)
os.environ["USAGE_DB"] = PROJ + "/usage_sb.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_sb.db"
os.environ["WEB_DATA_DIR"] = "web_data_sb"
from fastapi.testclient import TestClient

import accounts
import book
import web_api

fails = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{x}]"))
    if not c:
        fails.append(n)


def rec(t):
    L = ["🍽 " + t, "", "⏱ Время: 30 мин | 👥 Порций: 2 | 🔥 Способ: Плита", "", "*Пищевая ценность (на 1 порцию):*",
         "Калорийность: 400 ккал | Б: 20 г | Ж: 10 г | У: 50 г", "", "*Продукты:*", "- Гречка — 100 г", "- Грибы — 200 г", "", "*Инструкция:*",
         "1. Подготовьте продукты: гречку промойте несколько раз, грибы нарежьте тонкими ломтиками и обсушите.",
         "2. Обжарьте грибы на сухой сковороде до золотистого цвета, добавьте гречку и воду, варите 20 минут под крышкой.",
         "3. Подавайте горячим, посолив по вкусу и добавив немного зелени для свежести и аромата."]
    return chr(10).join(L)


D = {"cuisine": "classic", "time": "30 мин", "servings": 2, "portion": "normal", "appliance": ["stove"]}
a = book.add(D, "Гречка с грибами", rec("Гречка с грибами"), [])
b = book.add(D, "Плохой суп", rec("Плохой суп"), [])
for i in range(4):
    accounts._q("INSERT INTO book_votes(book_id, uid, vote, ts) VALUES(?,?,?,?)", (b, f"u{i}", -1, 1.0))
cl = TestClient(web_api.app)
r = cl.get(f"/recept/{a}-grechka-s-gribami")
check("страница рецепта открывается без входа", r.status_code == 200 and "Гречка с грибами" in r.text)
m = re.search(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S)
ld = json.loads(m.group(1)) if m else {}
check("разметка Recipe: название, время, ингредиенты, шаги, калории", ld.get("@type") == "Recipe" and ld.get("totalTime") == "PT30M"
      and len(ld.get("recipeIngredient", [])) == 2 and len(ld.get("recipeInstructions", [])) == 3 and "400" in ld.get("nutrition", {}).get("calories", ""), str(ld)[:200])
check("canonical и без noindex", 'rel="canonical"' in r.text and "noindex" not in r.text.lower() and "noindex" not in r.headers.get("x-robots-tag", ""))
check("адрес только по номеру тоже открывается", cl.get(f"/recept/{a}").status_code == 200)
check("скрытый плохими оценками рецепт — 404", cl.get(f"/recept/{b}-plohoy-sup").status_code == 404)
check("мусорный адрес — 404", cl.get("/recept/abc").status_code == 404 and cl.get("/recept/999999").status_code == 404)
check("картинка публичная", cl.get(f"/recept-img/{a}.png").status_code == 200 and cl.get(f"/recept-img/{b}.png").status_code == 404)
idx = cl.get("/recepty")
check("список: есть рецепт, скрытого нет", idx.status_code == 200 and "Гречка с грибами" in idx.text and "Плохой суп" not in idx.text)
sm = cl.get("/sitemap.xml").text
check("карта сайта: список и страница рецепта, скрытого нет", "/recepty<" in sm and f"/recept/{a}-grechka-s-gribami" in sm and f"/recept/{b}-" not in sm)
check("robots не закрывает рецепты", "/recept" not in cl.get("/robots.txt").text)
print("ИТОГ:", "ВСЁ ОК" if not fails else f"упало {len(fails)}")
sys.exit(1 if fails else 0)
