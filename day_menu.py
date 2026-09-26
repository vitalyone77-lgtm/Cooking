"""
Меню на день: несколько приёмов пищи (завтрак/обед/ужин и опционально перекус/бранч)
одним запросом — параллельно генерируем рецепт под каждый приём пищи (переиспользуя
обычный конвейер search_recipes -> generate_recipe -> extract_shopping_terms) и
собираем ОДИН общий список покупок на все блюда сразу (без дублей).

Полные тексты рецептов держим в памяти процесса (по аналогии с last_recipe.py) —
этого достаточно, чтобы кнопки "Показать рецепт" под сводкой открывали нужный текст.
"""
import asyncio
import logging

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards import MEAL_LABELS, MEAL_KCAL_SHARE
from search import search_recipes, format_results_for_prompt
from ai import generate_recipe
from shopping import extract_shopping_terms, extract_dish_title, format_shopping_message

logger = logging.getLogger(__name__)

# {chat_id: {meal_key: {"title": str, "text": str}}}
_store: dict[int, dict[str, dict]] = {}


def _meal_label_text(meal_key: str) -> str:
    # Без эмодзи, для подстановки в текстовые поля запроса ("Завтрак", "Обед", ...)
    return MEAL_LABELS[meal_key].split(" ", 1)[1]


def _build_meal_data(meal_key: str, base_data: dict, selected_meals: list[str]) -> dict:
    """
    Готовит данные анкеты под конкретный приём пищи: подмешивает его название в
    предпочтения (чтобы и поиск, и промпт LLM понимали контекст) и, если задана общая
    цель по КБЖУ на день, добавляет долю для этого приёма пищи.
    """
    data = dict(base_data)
    label_text = _meal_label_text(meal_key)

    preferred = (base_data.get("preferred") or "").strip()
    data["preferred"] = f"{label_text}. {preferred}" if preferred else label_text

    day_goal = (base_data.get("macro_goal") or "").strip()
    if day_goal:
        total_share = sum(MEAL_KCAL_SHARE.get(k, 0.2) for k in selected_meals) or 1
        share_pct = round(MEAL_KCAL_SHARE.get(meal_key, 0.2) / total_share * 100)
        data["macro_goal"] = (
            f"{day_goal} — это цель на ВЕСЬ день; приём пищи «{label_text}» должен "
            f"составлять примерно {share_pct}% от этой суммарной дневной цели"
        )

    return data


async def _generate_one_meal(meal_key: str, base_data: dict, selected_meals: list[str]) -> dict:
    data = _build_meal_data(meal_key, base_data, selected_meals)
    try:
        search_query, results = await search_recipes(data)
        results_text = format_results_for_prompt(results)
        recipe_text = await generate_recipe(data, search_query, results_text)
        recipe_text, shopping_terms = extract_shopping_terms(recipe_text)
        if not recipe_text.strip():
            raise RuntimeError("пустой ответ ИИ")
        dish_title = extract_dish_title(recipe_text) or _meal_label_text(meal_key)
        return {
            "meal_key": meal_key,
            "ok": True,
            "title": dish_title,
            "text": recipe_text,
            "shopping_terms": shopping_terms,
        }
    except Exception as e:
        logger.exception(f"Не удалось собрать блюдо для приёма пищи {meal_key}")
        return {
            "meal_key": meal_key,
            "ok": False,
            "title": _meal_label_text(meal_key),
            "text": f"😔 Не получилось подобрать рецепт для этого приёма пищи.\nПричина: {e}",
            "shopping_terms": [],
        }


def _merge_shopping_terms(per_meal_terms: list[list[str]]) -> list[str]:
    """Объединяет продукты со всех блюд, убирая повторы (без учёта регистра)."""
    seen = set()
    merged = []
    for terms in per_meal_terms:
        for term in terms:
            key = term.strip().lower()
            if key and key not in seen:
                seen.add(key)
                merged.append(term.strip())
    return merged


async def generate_day_menu(chat_id: int, selected_meals: list[str], base_data: dict) -> dict:
    """
    Запускает генерацию всех выбранных приёмов пищи параллельно, сохраняет полные
    тексты для последующего показа по кнопке и возвращает сводку для итогового
    сообщения: список (meal_key, title, ok) и общий текст списка покупок.
    """
    results = await asyncio.gather(
        *[_generate_one_meal(meal_key, base_data, selected_meals) for meal_key in selected_meals]
    )

    _store[chat_id] = {r["meal_key"]: {"title": r["title"], "text": r["text"]} for r in results}

    shopping_message = format_shopping_message(
        _merge_shopping_terms([r["shopping_terms"] for r in results if r["ok"]])
    )

    return {"meals": results, "shopping_message": shopping_message}


def get_meal_text(chat_id: int, meal_key: str) -> dict | None:
    return _store.get(chat_id, {}).get(meal_key)


def day_menu_summary_kb(results: list[dict]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for r in results:
        emoji_label = MEAL_LABELS[r["meal_key"]]
        short_title = r["title"] if len(r["title"]) <= 30 else r["title"][:27] + "..."
        b.button(text=f"{emoji_label}: {short_title}", callback_data=f"daymenu:show:{r['meal_key']}")
    b.button(text="🔄 Собрать меню заново", callback_data="daymenu:start")
    b.adjust(1)
    return b.as_markup()
