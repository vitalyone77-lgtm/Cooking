"""Перекусы: два перекуса в меню, сытные требования в промптах, рацион «Сытный перекус»."""
import os
import sys

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
os.environ["WEB_DATA_DIR"] = "/tmp/ct/web_data_test3"

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


import keyboards as K
import cuisines
import prompts
import day_menu

check("два перекуса в выборе приёмов пищи", "snack" in K.MEAL_LABELS and "snack2" in K.MEAL_LABELS)
check("порядок дня: завтрак, перекус 1, обед, перекус 2, ужин", list(K.MEAL_LABELS) == ["breakfast", "brunch", "snack", "lunch", "snack2", "dinner"], str(list(K.MEAL_LABELS)))
check("подписи различимы", day_menu.meal_label("snack") == "Перекус 1" and day_menu.meal_label("snack2") == "Перекус 2")
kb = K.day_meals_kb(["breakfast", "snack", "snack2", "dinner"]) if hasattr(K, "day_meals_kb") else None
if kb is None:
    name = next(n for n in dir(K) if "meal" in n.lower() and n.endswith("_kb"))
    kb = getattr(K, name)(["breakfast", "snack", "snack2", "dinner"])
btxt = [b.text for r in kb.inline_keyboard for b in r]
check("в клавиатуре оба перекуса, выбранные с ✅", any("✅" in t and "Перекус 1" in t for t in btxt) and any("✅" in t and "Перекус 2" in t for t in btxt), str(btxt))

# доли калорий: 2 основных + 2 перекуса -> перекус заметно меньше основного
sel = ["breakfast", "snack", "snack2", "dinner"]
tot = sum(K.MEAL_KCAL_SHARE[k] for k in sel)
shares = {k: round(K.MEAL_KCAL_SHARE[k] / tot * 100) for k in sel}
check("доли: основные больше перекусов, сумма ~100%", shares["breakfast"] > shares["snack"] and 98 <= sum(shares.values()) <= 102, str(shares))

# рацион «Сытный перекус» для одного рецепта
check("рацион «Сытный перекус» в списке", cuisines.CUISINE_LABELS.get("snack") == "Сытный перекус")
ck = [b.text for r in K.cuisine_kb(True).inline_keyboard for b in r]
check("кнопка рациона есть в главном меню", any("Сытный перекус" in t for t in ck), str(ck))
up = prompts.build_user_prompt({"cuisine": "snack", "time": "15 мин", "servings": 1, "appliance_labels": ["Плита"]}, "q", "r")
check("в рецепт-промпте требования к перекусу", "20-30 г белка" in up and "без разогрева" in up and "НЕ делай перекус из одних быстрых углеводов" in up)

# меню на день: у перекусов — особые требования, у завтрака — нет
import asyncio
import search


async def fake_search(data):
    fake_search.queries.append(data.get("preferred"))
    return "q", []

fake_search.queries = []
day_menu.search_recipes = fake_search
info = asyncio.run(day_menu.prepare_meals_info(["breakfast", "snack", "snack2"], {"cuisine": "classic", "preferred": "курица"}))
check("у перекусов есть требования, у завтрака нет", info[0]["rule"] == "" and "20-30 г белка" in info[1]["rule"] and info[2]["rule"] == info[1]["rule"])
check("поиск перекуса — «сытный белковый перекус»", "сытный белковый перекус" in fake_search.queries[1] and "курица" in fake_search.queries[1], str(fake_search.queries))
dp = prompts.build_day_menu_user_prompt(info, {"cuisine": "classic"})
check("в промпте меню — особые требования перекусов", dp.count("ОСОБЫЕ ТРЕБОВАНИЯ К ЭТОМУ ПРИЁМУ ПИЩИ") == 2 and "Приём пищи 2: Перекус 1" in dp)
wp = prompts.build_week_basket_user_prompt(["Завтрак", "Перекус 1", "Перекус 2", "Ужин"], 3, {"cuisine": "classic"})
check("в промпте плана — корзина с учётом перекусов", "ПЕРЕКУСЫ" in wp and "20-30 г белка" in wp)
wp2 = prompts.build_week_basket_user_prompt(["Завтрак", "Ужин"], 3, {"cuisine": "classic"})
check("без перекусов лишнего нет", "ПЕРЕКУСЫ" not in wp2)

# веб
import web_api
from fastapi.testclient import TestClient
c = TestClient(web_api.app)
meta = c.get("/api/meta").json()
keys = [m["key"] for m in meta["meals"]]
check("веб: перекусы в выборе приёмов пищи", keys == ["breakfast", "brunch", "snack", "lunch", "snack2", "dinner"], str(keys))
check("веб: рацион «Сытный перекус»", any(x["key"] == "snack" for x in meta["cuisines"]))

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
sys.exit(1 if fails else 0)
