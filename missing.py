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


async def adapt_recipe(recipe_text: str, missing: list[str]) -> tuple[str, list[str]]:
    """Один рецепт: замена недостающих продуктов (или другое блюдо, если без них никак). -> (текст, продукты для поиска)."""
    missing = clean_missing(missing)
    user = (
        "Вот готовый рецепт. ЭТИХ ПРОДУКТОВ НЕТ в магазине пользователя: " + ", ".join(missing) + ".\n\n"
        "Перепиши рецепт: замени их подходящими аналогами, которые легко найти в обычном российском магазине "
        "(вкус, тип питания и КБЖУ должны остаться близкими), и заново пересчитай граммовки, шаги и КБЖУ на порцию. "
        "Не используй недостающие продукты нигде в рецепте. Если без них блюдо не получится — предложи другое "
        "подходящее блюдо с теми же условиями и первой строкой ответа напиши «ℹ️ Без этих продуктов блюдо не "
        "получится — предлагаю другое:». Оформи СТРОГО по формату из системного промпта, включая служебную строку "
        "ПРОДУКТЫ_СПИСОК в конце.\n"
        + season_line() + "\nРЕЦЕПТ:\n" + recipe_text
    )
    raw = await call_llm_with_fallback([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}])
    text, terms = extract_shopping_terms(raw)
    if not text.strip():
        raise RuntimeError("ИИ вернул пустой ответ")
    return text, terms


async def rebuild_day(chat_id: int, missing: list[str]) -> str:
    """Меню на день без недостающих продуктов. Возвращает режим: 'regen' (сборка заново) или 'adapt' (правка блюд)."""
    missing = clean_missing(missing)
    entry = day_menu._load_menu(chat_id)
    if not entry:
        raise RuntimeError("Меню на день не найдено — собери его заново")
    base = entry.get("base_data")
    if base:
        await day_menu.generate_day_menu(chat_id, entry["order"], with_excluded(base, missing))
        return "regen"
    # Меню собрано до появления сохранённых параметров — правим каждое блюдо по отдельности
    keys = [k for k in entry["order"] if entry["meals"].get(k, {}).get("ok")]
    results = await asyncio.gather(*[adapt_recipe(entry["meals"][k]["text"], missing) for k in keys])
    for k, (text, terms) in zip(keys, results):
        entry["meals"][k] = {"title": day_menu.extract_dish_title(text) or entry["meals"][k]["title"], "text": text, "ok": True}
    entry["shopping_terms"] = day_menu._merge_shopping_terms([t for _, t in results])
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
