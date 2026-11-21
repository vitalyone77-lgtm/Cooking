"""
Публичные страницы рецептов из книги для поиска (Google, Яндекс): /recepty (список) и /recept/<номер>-<название>.
На странице рецепт текстом, карточка-картинка и разметка schema.org/Recipe (так поисковик показывает рецепт с калориями и временем).
В книге нет данных о людях, поэтому страницы публичные. Скрытые из-за плохих оценок рецепты не показываем и в карту сайта не кладём.
"""
import html
import json
import re

import accounts
import book

_TR = dict(zip("абвгдежзийклмнопрстуфхцчшщыэюя",
               ["a", "b", "v", "g", "d", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f", "h", "c", "ch", "sh", "sch", "y", "e", "yu", "ya"]))


def slug(title: str) -> str:
    s = "".join(_TR.get(ch, ch) for ch in (title or "").lower().replace("ё", "е"))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:50] or "recipe"


def path(row: dict) -> str:
    return f"/recept/{row['id']}-{slug(row['title'])}"


def visible_rows(limit: int = 2000) -> list[dict]:
    rows = accounts._q(f"SELECT {book.COLS} FROM book WHERE " + book.SCORE + " > ? ORDER BY id DESC LIMIT ?", (book.HIDE_SCORE, limit), all_=True)
    return [book._row(r) for r in rows]


def get_visible(book_id: int) -> dict | None:
    r = book.get(book_id)
    if not r:
        return None
    score = accounts._q("SELECT " + book.SCORE.replace("book.id", "?") + " FROM (SELECT 1)", (int(book_id),), one=True)
    return r if (score[0] if score else 0) > book.HIDE_SCORE else None


def _parts(text: str) -> tuple[list[str], list[str]]:
    ings, steps, mode = [], [], ""
    for ln in (text or "").splitlines():
        s = ln.strip()
        low = s.lower().strip("*: ")
        if low.startswith(("продукты", "ингредиенты")):
            mode = "i"
        elif low.startswith("инструкция") or low.startswith("приготовление"):
            mode = "s"
        elif mode == "i" and s[:1] in "-•*" and len(s) > 2:
            ings.append(s.lstrip("-•* ").strip())
        elif mode == "s":
            m = re.match(r"^\d+[.)]\s*(.+)", s)
            if m:
                steps.append(m.group(1).strip())
    return ings, steps


def _ld(row: dict, url: str, img: str) -> str:
    ings, steps = _parts(row["text"])
    d = {"@context": "https://schema.org", "@type": "Recipe", "name": row["title"], "image": [img], "inLanguage": "ru",
         "recipeIngredient": ings, "recipeInstructions": [{"@type": "HowToStep", "text": x} for x in steps]}
    if row.get("minutes"):
        d["totalTime"] = f"PT{int(row['minutes'])}M"
    if row.get("servings"):
        d["recipeYield"] = f"{row['servings']} порц."
    if row.get("kcal"):
        d["nutrition"] = {"@type": "NutritionInformation", "calories": f"{row['kcal']} ккал", "servingSize": "1 порция",
                          **({"proteinContent": f"{row['protein']} г"} if row.get("protein") else {}),
                          **({"fatContent": f"{row['fat']} г"} if row.get("fat") else {}),
                          **({"carbohydrateContent": f"{row['carb']} г"} if row.get("carb") else {})}
    return json.dumps(d, ensure_ascii=False).replace("</", "<" + chr(92) + "/")


CSS = ("body{margin:0;background:#FBF7F2;color:#2B2622;font:17px/1.6 -apple-system,Segoe UI,Roboto,Arial,sans-serif}"
       "main{max-width:720px;margin:0 auto;padding:16px}h1{font-size:28px;line-height:1.25;margin:12px 0}"
       "img{width:100%;height:auto;border-radius:16px;border:1px solid #E7DED3}.recipe{white-space:pre-wrap;word-wrap:break-word;margin:16px 0}"
       ".cta{background:#E6F0EA;border-radius:16px;padding:16px;margin:20px 0}.btn{display:block;text-align:center;background:#2F6B4F;color:#fff;"
       "text-decoration:none;font-weight:600;padding:14px 16px;border-radius:14px;margin-top:10px}.note{color:#6B625A;font-size:14px}a{color:#2F6B4F}"
       "@media (prefers-color-scheme:dark){body{background:#1A1714;color:#F1EAE2}.cta{background:#24352C}.note{color:#B5AA9E}a{color:#7CC5A0}}")


def render(row: dict, base: str, bot_username: str) -> str:
    url = base + path(row)
    img = f"{base}/recept-img/{row['id']}.png"
    t = html.escape(row["title"])
    m = re.search(r"Калорийность:[^\n]+", row["text"] or "")
    desc = html.escape(f"Рецепт: {row['title']}." + (f" {m.group(0).strip()}." if m else "") + " Продукты и пошаговое приготовление.")
    bot = f"https://t.me/{html.escape(bot_username)}?start=ref_site"
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{t}: рецепт и КБЖУ | Кухонный помощник</title>
<meta name="description" content="{desc}"><link rel="canonical" href="{url}">
<meta property="og:type" content="article"><meta property="og:site_name" content="Кухонный помощник"><meta property="og:title" content="{t}">
<meta property="og:description" content="{desc}"><meta property="og:url" content="{url}"><meta property="og:image" content="{img}">
<meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json">{_ld(row, url, img)}</script>
<style>{CSS}</style></head><body><main>
<p class="note"><a href="{base}/">Кухонный помощник</a> · <a href="{base}/recepty">Все рецепты</a></p>
<h1>{t}</h1>
<img src="{img}" width="1200" height="630" alt="{t}">
<div class="recipe">{html.escape(row["text"])}</div>
<div class="cta"><strong>Хотите рецепт под свои продукты, время и цель по КБЖУ?</strong>
<a class="btn" href="{base}/?utm_source=recept">Открыть сайт</a><a class="btn" href="{bot}">Бот в Telegram</a></div>
<p class="note">Калории и БЖУ приблизительные и не заменяют рекомендации врача. Если у вас аллергия, проверяйте состав.</p>
</main></body></html>"""


def render_index(rows: list[dict], base: str) -> str:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(book.MEAL_TITLES.get(r["meal"], "Другое"), []).append(r)
    body = "".join(f"<h2>{html.escape(k)}</h2><ul>" + "".join(
        f'<li><a href="{base}{path(r)}">{html.escape(r["title"])}</a>' + (f' <span class="note">{r["kcal"]} ккал</span>' if r.get("kcal") else "") + "</li>"
        for r in v) + "</ul>" for k, v in by.items())
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Книга рецептов с КБЖУ | Кухонный помощник</title>
<meta name="description" content="Рецепты с калориями, белками, жирами и углеводами на порцию: завтраки, обеды, ужины, перекусы. Пошаговое приготовление и список продуктов.">
<link rel="canonical" href="{base}/recepty"><style>{CSS}</style></head><body><main>
<p class="note"><a href="{base}/">Кухонный помощник</a></p><h1>Книга рецептов</h1>{body}
</main></body></html>"""
