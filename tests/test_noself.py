"""Свой же новый рецепт из книги человеку не выпадает; названия виденных рецептов уходят ИИ как «не повторять»."""
import os
import sys

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
os.environ["USAGE_DB"] = PROJ + "/usage_ns.db"
os.environ["ACCOUNTS_DB"] = PROJ + "/accounts_ns.db"
for f in ("usage_ns.db", "accounts_ns.db", "accounts_ns.db-wal", "accounts_ns.db-shm"):
    if os.path.exists(f):
        os.remove(f)
import book

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
bid = book.add(D, "Гречка с грибами", rec("Гречка с грибами"), [])
check("рецепт добавлен", bool(bid))
check("пока не помечен, книга отдаёт его (так и было)", (book.find(1, D) or {}).get("id") == bid)
book.mark_seen(2, bid)
check("после отметки автору он не выпадает", book.find(2, D) is None)
check("названия виденных идут в «не повторять»", book.recent_titles(2) == ["Гречка с грибами"] and book.recent_titles(3) == [])
print("ИТОГ:", "ВСЁ ОК" if not fails else f"упало {len(fails)}")
sys.exit(1 if fails else 0)
