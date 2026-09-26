"""
Меню на день: несколько приёмов пищи (завтрак/обед/ужин и опционально перекус/бранч)
одним запросом. Сначала пробуем СОВМЕСТНУЮ генерацию — один LLM-вызов на все блюда сразу,
с явной инструкцией максимально переиспользовать продукты между блюдами (иначе на каждый
приём пищи выходит полностью свой набор продуктов, и список покупок раздувается). Если
совместный формат почему-то не распарсился (не тот формат/сбой LLM) — надёжности ради
откатываемся на старый способ: независимая параллельная генерация каждого блюда своим
вызовом (тогда продукты между блюдами уже не переиспользуются, зато результат гарантирован).
Общий список покупок собирается один раз на все блюда сразу (без дублей).

Последнее собранное меню на день хранится в JSON-файле (по аналогии с favorites.py) —
переживает перезапуск бота, поэтому кнопка "📅 Меню дня" в постоянном нижнем меню открывает
последнее меню даже на следующий день, а не теряет его при деплое. Сводка и открытый
рецепт — это ОДНО и то же сообщение: кнопка "Показать рецепт" редактирует его в развёрнутый
вид, а "⬅️ Свернуть" возвращает обратно к списку — чат не разрастается новыми сообщениями
на каждое открытие/закрытие блюда.
"""
import asyncio
import json
import logging
import threading
from pathlib import Path

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards import MEAL_LABELS, MEAL_KCAL_SHARE
from search import search_recipes, format_results_for_prompt
from ai import generate_recipe, call_llm_with_fallback
from prompts import DAY_MENU_SYSTEM_PROMPT, DAY_MENU_BLOCK_DELIMITER, build_day_menu_user_prompt
from shopping import extract_shopping_terms, extract_dish_title, format_shopping_message

logger = logging.getLogger(__name__)

DAY_MENU_FILE = Path(__file__).parent / "day_menu.json"
_lock = threading.Lock()


def _read_all() -> dict:
    if not DAY_MENU_FILE.exists():
        return {}
    try:
        with open(DAY_MENU_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Не удалось прочитать {DAY_MENU_FILE}: {e}")
        return {}


def _write_all(data: dict) -> None:
    with open(DAY_MENU_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _save_menu(chat_id: int, entry: dict) -> None:
    with _lock:
        all_data = _read_all()
        all_data[str(chat_id)] = entry
        _write_all(all_data)


def _load_menu(chat_id: int) -> dict | None:
    with _lock:
        return _read_all().get(str(chat_id))


def has_menu(chat_id: int) -> bool:
    return _load_menu(chat_id) is not None


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


def _meal_macro_goal_text(meal_key: str, base_data: dict, selected_meals: list[str]) -> str | None:
    day_goal = (base_data.get("macro_goal") or "").strip()
    if not day_goal:
        return None
    total_share = sum(MEAL_KCAL_SHARE.get(k, 0.2) for k in selected_meals) or 1
    share_pct = round(MEAL_KCAL_SHARE.get(meal_key, 0.2) / total_share * 100)
    label_text = _meal_label_text(meal_key)
    return f"{day_goal} на весь день; этот приём пищи («{label_text}») — примерно {share_pct}% от неё"


def _strip_meal_prefix(title: str, label_text: str) -> str:
    """Убирает префикс вида 'Завтрак: ' из заголовка блюда, если модель его добавила."""
    prefix = f"{label_text}:"
    if title.lower().startswith(prefix.lower()):
        return title[len(prefix):].strip()
    return title


async def _generate_combined(selected_meals: list[str], base_data: dict) -> list[dict]:
    """
    Один LLM-вызов на все блюда сразу — чтобы модель могла спланировать общий набор
    продуктов на день. Поднимает исключение при любом сбое парсинга/генерации, чтобы
    вызывающий код мог откатиться на независимую генерацию.
    """
    search_pairs = await asyncio.gather(
        *[search_recipes(_build_meal_data(mk, base_data, selected_meals)) for mk in selected_meals]
    )

    meals_info = [
        {
            "label": _meal_label_text(meal_key),
            "search_query": query,
            "search_results_text": format_results_for_prompt(results),
            "macro_goal": _meal_macro_goal_text(meal_key, base_data, selected_meals),
        }
        for meal_key, (query, results) in zip(selected_meals, search_pairs)
    ]

    messages = [
        {"role": "system", "content": DAY_MENU_SYSTEM_PROMPT},
        {"role": "user", "content": build_day_menu_user_prompt(meals_info, base_data)},
    ]
    max_tokens = min(8000, 1400 * len(selected_meals) + 600)

    raw = await call_llm_with_fallback(messages, max_tokens=max_tokens)

    blocks = [b.strip() for b in raw.split(DAY_MENU_BLOCK_DELIMITER)]
    if len(blocks) != len(selected_meals):
        raise ValueError(
            f"ожидалось {len(selected_meals)} блоков, получено {len(blocks)} — "
            "формат ответа LLM не совпал"
        )

    results = []
    for meal_key, block in zip(selected_meals, blocks):
        label_text = _meal_label_text(meal_key)
        recipe_text, shopping_terms = extract_shopping_terms(block)
        if not recipe_text.strip():
            raise ValueError(f"пустой блок для приёма пищи {meal_key}")
        dish_title = _strip_meal_prefix(extract_dish_title(recipe_text) or label_text, label_text)
        results.append({
            "meal_key": meal_key,
            "ok": True,
            "title": dish_title,
            "text": recipe_text,
            "shopping_terms": shopping_terms,
        })
    return results


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
    Собирает меню на день: сначала пробует совместную генерацию (один LLM-вызов, общий
    набор продуктов на все блюда), при сбое — откатывается на независимую генерацию
    каждого блюда отдельным вызовом. Сохраняет полные тексты для последующего
    разворачивания/сворачивания и возвращает сводку: список (meal_key, title, ok) и
    общий текст списка покупок.
    """
    try:
        results = await _generate_combined(selected_meals, base_data)
    except Exception as e:
        logger.warning(f"Совместная генерация меню на день не удалась ({e}), откат на независимую")
        results = await asyncio.gather(
            *[_generate_one_meal(meal_key, base_data, selected_meals) for meal_key in selected_meals]
        )

    _save_menu(chat_id, {
        "order": selected_meals,
        "meals": {r["meal_key"]: {"title": r["title"], "text": r["text"], "ok": r["ok"]} for r in results},
    })

    shopping_message = format_shopping_message(
        _merge_shopping_terms([r["shopping_terms"] for r in results if r["ok"]])
    )

    return {"meals": results, "shopping_message": shopping_message}


def build_summary_view(chat_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    """Свёрнутый вид: список приёмов пищи кнопками. None, если меню не найдено (истекло/не собрано)."""
    entry = _load_menu(chat_id)
    if not entry:
        return None

    order = entry["order"]
    meals = entry["meals"]
    ok_count = sum(1 for k in order if meals[k]["ok"])
    text = (
        f"📅 Меню на день готово ({ok_count}/{len(order)})! "
        "Выбери блюдо, чтобы увидеть полный рецепт:"
    )

    b = InlineKeyboardBuilder()
    for meal_key in order:
        m = meals[meal_key]
        emoji_label = MEAL_LABELS[meal_key]
        short_title = m["title"] if len(m["title"]) <= 30 else m["title"][:27] + "..."
        b.button(text=f"{emoji_label}: {short_title}", callback_data=f"daymenu:show:{meal_key}")
    b.button(text="🔄 Собрать меню заново", callback_data="daymenu:start")
    b.adjust(1)
    return text, b.as_markup()


def build_meal_view(chat_id: int, meal_key: str) -> tuple[str, InlineKeyboardMarkup] | None:
    """Развёрнутый вид: полный текст рецепта одного блюда + кнопка свернуть обратно."""
    entry = _load_menu(chat_id)
    if not entry or meal_key not in entry["meals"]:
        return None

    text = entry["meals"][meal_key]["text"]
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Свернуть", callback_data="daymenu:collapse")
    return text, b.as_markup()
