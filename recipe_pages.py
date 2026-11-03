"""
Публичная страница рецепта (/r/<адрес>): рецепт текстом и карточка-картинка. Нужна, чтобы ВКонтакте и мессенджеры показывали
под ссылкой красивое превью (метки og:*), а поисковики находили рецепт. Все данные экранируются.
"""
import html
import re

SLUG_RE = re.compile(r"^[a-z0-9-]{3,80}$")


def _description(title: str, body: str) -> str:
    m = re.search(r"Калорийность:[^\n]+", body or "")
    kcal = f" {m.group(0).strip()}." if m else ""
    return f"Рецепт: {title}.{kcal} Продукты и пошаговое приготовление. Подобрать рецепт под себя можно в боте и на сайте."[:300]


def render(recipe: dict, slug: str, base: str, bot_username: str) -> str:
    title = html.escape(recipe["title"])
    body = html.escape(recipe["body"])
    desc = html.escape(_description(recipe["title"], recipe["body"]))
    url = f"{base}/r/{slug}"
    img = f"{base}/rimg/{slug}.png"
    bot = f"https://t.me/{html.escape(bot_username)}?start=ref_site"
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}: рецепт и КБЖУ | Кухонный помощник</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="article"><meta property="og:site_name" content="Кухонный помощник">
<meta property="og:title" content="{title}"><meta property="og:description" content="{desc}">
<meta property="og:url" content="{url}"><meta property="og:image" content="{img}">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<style>
:root{{--bg:#FBF7F2;--fg:#2B2622;--muted:#6B625A;--line:#E7DED3;--primary:#2F6B4F;--on:#fff;--soft:#E6F0EA;color-scheme:light}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1A1714;--fg:#F1EAE2;--muted:#B5AA9E;--line:#3A342E;--primary:#7CC5A0;--on:#10261B;--soft:#24352C;color-scheme:dark}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:17px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}}
main{{max-width:720px;margin:0 auto;padding:16px}}
h1{{font-size:28px;line-height:1.25;margin:12px 0}}
img{{width:100%;height:auto;border-radius:16px;border:1px solid var(--line)}}
.recipe{{white-space:pre-wrap;word-wrap:break-word;margin:16px 0}}
.cta{{background:var(--soft);border-radius:16px;padding:16px;margin:20px 0}}
.btn{{display:block;text-align:center;background:var(--primary);color:var(--on);text-decoration:none;font-weight:600;padding:14px 16px;border-radius:14px;margin-top:10px}}
.note{{color:var(--muted);font-size:14px}}
</style></head><body><main>
<p class="note"><a href="/" style="color:inherit">Кухонный помощник</a></p>
<h1>{title}</h1>
<img src="{img}" width="1200" height="630" alt="{title}">
<div class="recipe">{body}</div>
<div class="cta"><strong>Хотите рецепт под свои продукты, время и цель по КБЖУ?</strong>
<a class="btn" href="{base}/?utm_source=recipe">Открыть сайт</a>
<a class="btn" href="{bot}">Бот в Telegram</a></div>
<p class="note">Калории и БЖУ приблизительные и не заменяют рекомендации врача. Если у вас аллергия, проверяйте состав.</p>
</main></body></html>"""
