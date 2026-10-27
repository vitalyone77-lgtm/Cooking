"""
«Нет каких-то продуктов в магазине»: ИИ заменяет их аналогами или переделывает блюдо / меню / план.
Логика без привязки к Telegram и вебу — её вызывают и бот (bot.py), и сайт (web_api.py).
"""
import asyncio
import logging

import day_menu
import week_menu
from ai import call_llm_with_fallback
from prompts import SYSTEM_PROMPT, season_line
from shopping import extract_shopping_terms

logger = logging.getLogger(__name__)

MAX_MISSING = 15


def clean_missing(names: list[str]) -> list[str]:
    """Названия без пустых и повторов (без учёта регистра), не больше MAX_MISSING."""
    seen, out = set(), []
    for n in names:
        n = " ".join(str(n).split()).strip(" .,;")
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out[:MAX_MISSING]


def with_excluded(data: dict, missing: list[str]) -> dict:
    """Копия параметров, где недостающие продукты добавлены к «исключить»."""
    out = dict(data or {})
    extra = ", ".join(missing)
    out["excluded"] = f"{out['excluded']}, {extra}" if out.get("excluded") else extra
    return out


REPLACE_RULES = (
    "ГЛАВНОЕ: это ТО ЖЕ САМОЕ БЛЮДО. Сохрани название, остальные продукты, их граммовки и шаги — меняй только то, что "
    "касается отсутствующих продуктов (человек, возможно, уже начал готовить).\n"
    "- Специи, приправы, зелень, соусы для подачи, посыпки, орехи/ягоды/фрукты для украшения, ваниль, корица и т.п. — "
    "это НЕ основа: либо просто убери (если блюдо и так вкусное), либо замени тем, что легко найти, и коротко скажи, "
    "что можно и без замены. Пример: нет корицы в овсяноблине → «можно без неё, или щепотка ванили / мускатного ореха; "
    "мёд добавит сладость, но не аромат корицы».\n"
    "- Основной продукт (белок, крупа, основа теста и т.п.) — замени ближайшим аналогом (курица → индейка, рис → "
    "булгур), пересчитай граммовки, время и КБЖУ.\n"
    "- ДРУГОЕ блюдо — ТОЛЬКО если отсутствует сама основа и у неё нет аналога (например, нет овсянки для овсяноблина и "
    "нечем заменить). Тогда первой строкой напиши «ℹ️ Без этого блюдо не получится — предлагаю другое:».\n"
    "Первой строкой ответа (кроме случая с другим блюдом) напиши коротко, что заменено: «🔄 Замена: корица → можно "
    "без неё или щепотка ванили». Дальше — весь рецепт СТРОГО по формату из системного промпта (КБЖУ пересчитай, "
    "если замена его меняет), включая служебную строку ПРОДУКТЫ_СПИСОК в конце. Отсутствующие продукты нигде не используй."
)


async def adapt_recipe(recipe_text: str, missing: list[str]) -> tuple[str, list[str]]:
    """Один рецепт: МИНИМАЛЬНАЯ замена недостающих (блюдо то же; другое — только если нет самой основы).
    -> (текст, продукты для поиска)."""
    missing = clean_missing(missing)
    user = (
        "Вот готовый рецепт. ЭТИХ ПРОДУКТОВ НЕТ у пользователя: " + ", ".join(missing) + ".\n\n"
        + REPLACE_RULES + "\n" + season_line() + "\nРЕЦЕПТ:\n" + recipe_text
    )
    raw = await call_llm_with_fallback([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}])
    text, terms = extract_shopping_terms(raw)
    if not text.strip():
        raise RuntimeError("ИИ вернул пустой ответ")
    return text, terms


def _mentions(text: str, names: list[str]) -> bool:
    """Есть ли в тексте блюда хоть один из продуктов (по основе первого слова: «корица» ~ «корицей»)."""
    t = (text or "").lower().replace("ё", "е")
    for n in names:
        w = n.lower().replace("ё", "е").split()[0] if n.split() else ""
        stem = w[:-1] if len(w) >= 4 and w[-1] in "аеиоуыэюяйь" else w
        if len(stem) >= 3 and stem in t:
            return True
    return False


async def rebuild_day(chat_id: int, missing: list[str]) -> str:
    """Меню на день без недостающих продуктов: правим ТОЛЬКО блюда, где они есть (минимальная замена),
    остальные не трогаем. Возвращает 'adapt'."""
    missing = clean_missing(missing)
    entry = day_menu._load_menu(chat_id)
    if not entry:
        raise RuntimeError("Меню на день не найдено — собери его заново")
    if entry.get("base_data"):
        entry["base_data"] = with_excluded(entry["base_data"], missing)    # и в следующий раз без них
    keys = [k for k in entry["order"] if entry["meals"].get(k, {}).get("ok")
            and _mentions(entry["meals"][k].get("text", ""), missing)]
    if not keys:
        keys = [k for k in entry["order"] if entry["meals"].get(k, {}).get("ok")]
    results = await asyncio.gather(*[adapt_recipe(entry["meals"][k]["text"], missing) for k in keys])
    for k, (text, terms) in zip(keys, results):
        entry["meals"][k] = {"title": day_menu.extract_dish_title(text) or entry["meals"][k]["title"], "text": text, "ok": True}
    old = [t for t in (entry.get("shopping_terms") or []) if not _mentions(t, missing)]
    entry["shopping_terms"] = day_menu._merge_shopping_terms([old] + [t for _, t in results])
    day_menu._save_menu(chat_id, entry)
    return "adapt"


async def adjust_plan(chat_id: int, missing: list[str]) -> str:
    """
    План на несколько дней. Пока ни один день не создан — корзина собирается заново без этих продуктов ('rebuild').
    Если дни уже создаются — готовые дни не трогаем (из-за остатков), продукты убираем из корзины и из будущих дней ('future').
    """
    missing = clean_missing(missing)
    state = week_menu._load(chat_id)
    if not state:
        raise RuntimeError("План питания не найден — составь его заново")
    if not state.get("days"):
        await week_menu.start_week_plan(chat_id, state["meal_keys"], state["days_total"],
                                        with_excluded(state["base_data"], missing))
        return "rebuild"
    state["base_data"] = with_excluded(state["base_data"], missing)
    for name in missing:
        for inv in (state["inventory"], state["inventory_initial"]):
            key = week_menu._match_inventory_key(inv, name)
            if key:
                inv.pop(key, None)
    week_menu._save(chat_id, state)
    return "future"
