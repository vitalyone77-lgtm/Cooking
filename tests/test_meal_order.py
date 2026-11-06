"""Порядок приёмов пищи и привязка блюд к ним; ясное время жарки."""
import os
import sys

PROJ = "/tmp/ct"
os.chdir(PROJ)
sys.path.insert(0, PROJ)
os.environ["WEB_DATA_DIR"] = "/tmp/ct/web_data_test5"

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


import keyboards as K
import day_menu
import prompts

check("sort_meals: порядок дня", K.sort_meals({"dinner", "snack2", "breakfast", "snack"}) == ["breakfast", "snack", "snack2", "dinner"], str(K.sort_meals({"dinner", "snack2", "breakfast", "snack"})))
check("sort_meals: мусор и повторы отброшены", K.sort_meals(["dinner", "dinner", "xx"]) == ["dinner"])

sel = ["breakfast", "snack", "dinner"]
b_break = "ПРИЁМ_ПИЩИ: Завтрак\n🍽 Каша\n\nПродукты:\n- овсянка — 40 г\nПРОДУКТЫ_СПИСОК: овсянка"
b_snack = "ПРИЁМ_ПИЩИ: Перекус 1\n🍽 Творог с орехами\n\nПродукты:\n- творог — 150 г\nПРОДУКТЫ_СПИСОК: творог"
b_dinner = "ПРИЁМ_ПИЩИ: Ужин\n🍽 Курица с гречкой\n\nПродукты:\n- курица — 150 г\nПРОДУКТЫ_СПИСОК: курица"

res = day_menu.parse_meal_blocks([b_break, b_snack, b_dinner], sel)
check("правильный порядок — как есть", [r["title"] for r in res] == ["Каша", "Творог с орехами", "Курица с гречкой"], str([r["title"] for r in res]))
# ИИ поменял местами перекус и ужин
res = day_menu.parse_meal_blocks([b_break, b_dinner, b_snack], sel)
check("перепутанный порядок исправлен по меткам", [(r["meal_key"], r["title"]) for r in res] ==
      [("breakfast", "Каша"), ("snack", "Творог с орехами"), ("dinner", "Курица с гречкой")], str([(r["meal_key"], r["title"]) for r in res]))
check("метка не попадает в текст рецепта", all("ПРИЁМ_ПИЩИ" not in r["text"] for r in res))
# метки в другом оформлении
res = day_menu.parse_meal_blocks(["**ПРИЁМ_ПИЩИ: Завтрак**\n" + b_break.split("\n", 1)[1], "ПРИЁМ_ПИЩИ: ужин\n" + b_dinner.split("\n", 1)[1],
                                  "ПРИЁМ ПИЩИ: Перекус 1\n" + b_snack.split("\n", 1)[1]], sel)
check("метки с оформлением и другим регистром", [r["meal_key"] for r in res] == ["breakfast", "snack", "dinner"], str([r["meal_key"] for r in res]))
# без меток — как раньше по позициям
res = day_menu.parse_meal_blocks([x.split("\n", 1)[1] for x in (b_break, b_snack, b_dinner)], sel)
check("без меток — по позициям", [r["title"] for r in res] == ["Каша", "Творог с орехами", "Курица с гречкой"])
# метки не сходятся с выбором — по позициям, ничего не ломается
res = day_menu.parse_meal_blocks([b_break, b_snack, b_snack], sel)
check("повторная метка — по позициям, без падения", len(res) == 3)

dp = prompts.DAY_MENU_SYSTEM_PROMPT
check("в промпте меню — требование метки ПРИЁМ_ПИЩИ", "ПРИЁМ_ПИЩИ:" in dp and "Перекус 1" in dp)
check("в промптах — правило про стороны жарки", "КАЖДОЙ стороны" in prompts.SYSTEM_PROMPT and "КАЖДОЙ стороны" in dp)

import web_api
p = web_api.Params(appliance=["stove"], meals=["dinner", "snack", "breakfast", "snack2"])
check("веб: порядок приёмов пищи канонический", p.meal_keys() == ["breakfast", "snack", "snack2", "dinner"], str(p.meal_keys()))

print("\nИТОГ:", "ВСЁ ОК" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails))
sys.exit(1 if fails else 0)
