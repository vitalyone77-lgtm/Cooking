"""
Меню на несколько дней (пилот на 3 дня, см. config.WEEK_MENU_DAYS).

В отличие от "Меню на день", здесь НЕ генерируется весь период рецептами заранее — это
ненадёжно (LLM плохо держит в голове точный остаток продуктов на 20+ блюд вперёд, а любое
отклонение человека от плана — пропущенный день, другое настроение — обесценивает готовые
наперёд тексты). Вместо этого:

1. Один LLM-вызов сразу считает ТОЛЬКО продуктовую корзину на весь период (базовые продукты
   с граммовками + советы по хранению/разморозке) — это даёт список покупок сразу.
2. Остаток продуктов — обычные данные в JSON, вычитаются кодом (арифметика надёжна),
   а не памятью модели.
3. Рецепты каждого дня генерируются ПО ФАКТУ (когда пользователь жмёт "Готовить день N"),
   с учётом того, что реально осталось, и того, что уже готовили — чтобы не повторяться.
   Технически это тот же "совместный" вызов, что у day_menu.py (все приёмы пищи дня одним
   запросом ради переиспользования продуктов), плюс доп. блок с фактическим расходом,
   который используется, чтобы вычесть из остатка.

Прогресс ("что уже приготовлено") хранится как плоский список слотов (день, приём пищи) с
указателем на следующий неприготовленный — кнопка в постоянном нижнем меню открывает именно
его, а не сводку.
"""
import json
import logging
import threading
from pathlib import Path

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

import day_menu
from ai import call_llm_with_fallback
from keyboards import MEAL_LABELS
from prompts import (
    WEEK_BASKET_SYSTEM_PROMPT,
    build_week_basket_user_prompt,
    DAY_MENU_SYSTEM_PROMPT,
    DAY_MENU_BLOCK_DELIMITER,
    build_day_menu_user_prompt,
    build_week_day_extra_instructions,
)
from shopping import extract_base_products, extract_used_base_products, format_shopping_message

logger = logging.getLogger(__name__)

WEEK_MENU_FILE = Path(__file__).parent / "week_menu.json"
_lock = threading.Lock()

# Сколько истории блюд показывать модели, чтобы не повторялась (и не раздувать промпт)
HISTORY_LIMIT = 6


def _read_all() -> dict:
    if not WEEK_MENU_FILE.exists():
        return {}
    try:
        with open(WEEK_MENU_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Не удалось прочитать {WEEK_MENU_FILE}: {e}")
        return {}


def _write_all(data: dict) -> None:
    with open(WEEK_MENU_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _save(chat_id: int, state: dict) -> None:
    with _lock:
        all_data = _read_all()
        all_data[str(chat_id)] = state
        _write_all(all_data)


def _load(chat_id: int) -> dict | None:
    with _lock:
        return _read_all().get(str(chat_id))


def has_plan(chat_id: int) -> bool:
    return _load(chat_id) is not None


def _flat_slots(state: dict) -> list[tuple[int, str]]:
    return [
        (day_num, meal_key)
        for day_num in range(1, state["days_total"] + 1)
        for meal_key in state["meal_keys"]
    ]


async def start_week_plan(chat_id: int, meal_keys: list[str], days_total: int, base_data: dict) -> tuple[str, str]:
    """
    Считает продуктовую корзину на весь период, сохраняет начальное состояние плана и
    возвращает (человекочитаемый текст со списком покупок и советами по хранению,
    сообщение со ссылками на поиск каждого продукта в Пятёрочке).
    """
    meal_labels = [day_menu.meal_label(mk) for mk in meal_keys]
    messages = [
        {"role": "system", "content": WEEK_BASKET_SYSTEM_PROMPT},
        {"role": "user", "content": build_week_basket_user_prompt(meal_labels, days_total, base_data)},
    ]
    raw = await call_llm_with_fallback(messages, max_tokens=2000)

    display_text, inventory = extract_base_products(raw)
    if not inventory:
        raise ValueError("не удалось распознать продуктовую корзину в ответе ИИ")

    _save(chat_id, {
        "base_data": base_data,
        "meal_keys": meal_keys,
        "days_total": days_total,
        "inventory": dict(inventory),
        "inventory_initial": dict(inventory),
        "basket_text": display_text,
        "history_titles": [],
        "days": {},
        "current_day": 1,
        "progress_index": 0,
    })

    shopping_message = format_shopping_message(list(inventory.keys()), chat_id)
    return display_text, shopping_message


def _match_inventory_key(inventory: dict, name: str) -> str | None:
    name_l = name.strip().lower()
    if name_l in inventory:
        return name_l
    for key in inventory:
        if key in name_l or name_l in key:
            return key
    return None


async def _generate_day(chat_id: int, state: dict) -> dict:
    """Генерирует все приёмы пищи ОДНОГО (следующего по очереди) дня и вычитает остаток."""
    day_number = state["current_day"]
    meal_keys = state["meal_keys"]
    base_data = state["base_data"]

    meals_info = await day_menu.prepare_meals_info(meal_keys, base_data)

    # Лимит на сегодня считаем в коде (надёжная арифметика), а не полагаемся на то, что
    # модель сама правильно поделит остаток на оставшиеся дни — именно это в тесте привело
    # к тому, что курицу съедали за 2 дня из 3, ничего не оставляя на третий.
    days_remaining = state["days_total"] - day_number + 1
    inventory_text = ", ".join(
        f"{name} — всего осталось {amount} г/шт, лимит на сегодня ~{max(1, amount // days_remaining)} г/шт"
        for name, amount in state["inventory"].items() if amount > 0
    )
    history_text = "; ".join(state["history_titles"][-HISTORY_LIMIT:])

    user_prompt = build_day_menu_user_prompt(meals_info, base_data) + build_week_day_extra_instructions(
        day_number, state["days_total"], inventory_text, history_text, DAY_MENU_BLOCK_DELIMITER
    )
    messages = [
        {"role": "system", "content": DAY_MENU_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    max_tokens = min(8000, 1400 * len(meal_keys) + 900)

    raw = await call_llm_with_fallback(messages, max_tokens=max_tokens)

    blocks = [b.strip() for b in raw.split(DAY_MENU_BLOCK_DELIMITER)]
    if len(blocks) != len(meal_keys) + 1:
        raise ValueError(
            f"ожидалось {len(meal_keys) + 1} блоков (блюда + расход), получено {len(blocks)}"
        )

    meal_blocks, usage_block = blocks[:-1], blocks[-1]
    meal_results = day_menu.parse_meal_blocks(meal_blocks, meal_keys)
    used = extract_used_base_products(usage_block)

    for name, amount in used.items():
        key = _match_inventory_key(state["inventory"], name)
        if key:
            state["inventory"][key] = max(0, state["inventory"][key] - amount)
        else:
            logger.warning(f"Не нашёл '{name}' из ИСПОЛЬЗОВАНО_БАЗОВЫЕ в остатках плана {chat_id}")

    day_meals = {r["meal_key"]: {"title": r["title"], "text": r["text"]} for r in meal_results}
    # В ссылки на докупку выносим только то, чего НЕТ в основной корзине (специи/соусы и т.п.,
    # разовые под конкретное блюдо) — базовые продукты уже получили свою ссылку в списке покупок
    # при сборке плана, повторять их здесь означало бы дублировать те же ссылки на каждый день.
    all_terms = _merge_terms(r["shopping_terms"] for r in meal_results)
    extra_terms = [t for t in all_terms if not _match_inventory_key(state["inventory_initial"], t)]
    day_shopping_message = format_shopping_message(extra_terms, chat_id)
    state["days"][str(day_number)] = {
        "order": meal_keys,
        "meals": day_meals,
        "shopping_message": day_shopping_message,
    }
    state["history_titles"].extend(r["title"] for r in meal_results)
    state["current_day"] = day_number + 1

    _save(chat_id, state)
    return state


def _merge_terms(term_lists) -> list[str]:
    """Объединяет продукты из нескольких блюд, убирая повторы (без учёта регистра)."""
    seen = set()
    merged = []
    for terms in term_lists:
        for term in terms:
            key = term.strip().lower()
            if key and key not in seen:
                seen.add(key)
                merged.append(term.strip())
    return merged


async def ensure_slot(chat_id: int, index: int) -> dict:
    """
    Гарантирует, что день, которому принадлежит слот с этим индексом, уже сгенерирован
    (генерирует по требованию, если это следующий по очереди день), и возвращает срез.
    Поднимает ValueError, если индекс за пределами плана, RuntimeError — если запрошен
    день "из будущего" не по порядку (такого в UI быть не должно, но на всякий случай).
    """
    state = _load(chat_id)
    if not state:
        raise RuntimeError("План питания не найден")

    slots = _flat_slots(state)
    if not (0 <= index < len(slots)):
        raise ValueError("индекс слота вне диапазона плана")

    day_number, meal_key = slots[index]
    just_generated = False
    if str(day_number) not in state["days"]:
        if day_number != state["current_day"]:
            raise RuntimeError("нельзя открыть день вне очереди — сначала приготовь предыдущие")
        state = await _generate_day(chat_id, state)
        just_generated = True

    day_entry = state["days"][str(day_number)]
    return {
        "index": index,
        "total_slots": len(slots),
        "day_number": day_number,
        "days_total": state["days_total"],
        "meal_key": meal_key,
        "just_generated": just_generated,
        "shopping_message": day_entry.get("shopping_message", ""),
        "meal": day_entry["meals"][meal_key],
        "is_next_uncooked": index == state["progress_index"],
    }


def mark_done(chat_id: int, index: int) -> None:
    """Отмечает слот как приготовленный и двигает указатель прогресса вперёд, если нужно."""
    state = _load(chat_id)
    if not state:
        return
    if index >= state["progress_index"]:
        state["progress_index"] = index + 1
    _save(chat_id, state)


def build_slot_view(chat_id: int, slot: dict) -> tuple[str, InlineKeyboardMarkup]:
    day_number = slot["day_number"]
    days_total = slot["days_total"]
    meal_key = slot["meal_key"]
    index = slot["index"]
    total_slots = slot["total_slots"]

    header = f"📆 День {day_number}/{days_total} — {MEAL_LABELS[meal_key]}\n\n"
    text = header + slot["meal"]["text"]

    b = InlineKeyboardBuilder()
    nav_row = []
    if index > 0:
        nav_row.append(("◀ Пред.", f"weekmenu:slot:{index - 1}"))
    if index < total_slots - 1:
        nav_row.append(("След. ▶", f"weekmenu:slot:{index + 1}"))
    for text_label, cb in nav_row:
        b.button(text=text_label, callback_data=cb)
    if index < total_slots - 1:
        b.button(text="✅ Готово — следующий рецепт", callback_data=f"weekmenu:done:{index}")
    else:
        b.button(text="✅ Готово — план завершён", callback_data=f"weekmenu:done:{index}")
    b.button(text="⬅️ К плану", callback_data=f"weekmenu:open:{day_number}")
    b.adjust(2, 1, 1)
    return text, b.as_markup()


def _default_open_day(state: dict) -> int:
    n = max(1, len(state["meal_keys"]))
    return min(state["progress_index"] // n + 1, state["days_total"])


def build_status_view(chat_id: int, open_day: int | None = None) -> tuple[str, InlineKeyboardMarkup] | None:
    """
    План по дням в виде раскрывающегося списка (аккордеон): заголовок каждого дня — кнопка,
    по нажатию день раскрывается (блюда-кнопки + «Готовить»), остальные сворачиваются.
    open_day=None — раскрыть день, на котором остановился прогресс; open_day=0 — всё свёрнуто.
    """
    state = _load(chat_id)
    if not state:
        return None

    days_total = state["days_total"]
    meal_keys = state["meal_keys"]
    n = len(meal_keys)
    if open_day is None:
        open_day = _default_open_day(state)

    cooked = min(state["progress_index"], days_total * n)
    remaining = [f"{name} — {amount}" for name, amount in state["inventory"].items() if amount > 0]
    lines = [f"📆 План питания на {days_total} дня — приготовлено блюд: {cooked}/{days_total * n}"]
    if remaining:
        lines.append("Остатки продуктов: " + ", ".join(remaining))
    text = "\n".join(lines)

    b = InlineKeyboardBuilder()
    for day in range(1, days_total + 1):
        first = (day - 1) * n
        done_count = max(0, min(n, state["progress_index"] - first))
        if done_count == n:
            badge = "✅"
        elif str(day) in state["days"]:
            badge = f"{done_count}/{n}"
        elif day == state["current_day"]:
            badge = "новый"
        else:
            badge = "🔒"
        is_open = day == open_day
        arrow = "▼" if is_open else "▶"
        b.button(text=f"{arrow} День {day} · {badge}",
                 callback_data=f"weekmenu:open:{0 if is_open else day}")
        if is_open:
            if str(day) in state["days"]:
                for j, meal_key in enumerate(meal_keys):
                    m = state["days"][str(day)]["meals"][meal_key]
                    short_title = m["title"] if len(m["title"]) <= 30 else m["title"][:27] + "..."
                    mark = "✅ " if first + j < state["progress_index"] else ""
                    b.button(text=f"   {mark}{MEAL_LABELS[meal_key]}: {short_title}",
                             callback_data=f"weekmenu:slot:{first + j}")
            elif day == state["current_day"]:
                b.button(text=f"   🍳 Приготовить меню дня {day}", callback_data="weekmenu:cook_next")
            else:
                b.button(text="   🔒 Откроется после предыдущего дня", callback_data="weekmenu:noop")
    b.button(text="🛒 Корзина и магазины", callback_data="weekmenu:basket")
    if state["current_day"] > days_total and state["progress_index"] >= days_total * n:
        b.button(text="🎉 Собрать новый план", callback_data="weekmenu:start")
    b.adjust(1)
    return text, b.as_markup()


def get_basket_info(chat_id: int) -> dict | None:
    """Данные для экрана «Корзина»: текст корзины от ИИ, продукты, сохранённый расчёт цен."""
    state = _load(chat_id)
    if not state:
        return None
    return {
        "basket_text": state.get("basket_text", ""),
        "inventory_initial": state["inventory_initial"],
        "priced": state.get("priced_basket", ""),
    }


def save_priced_basket(chat_id: int, text: str) -> None:
    state = _load(chat_id)
    if not state:
        return
    state["priced_basket"] = text
    _save(chat_id, state)


def day_of_slot(chat_id: int, index: int) -> int | None:
    state = _load(chat_id)
    if not state:
        return None
    slots = _flat_slots(state)
    return slots[index][0] if 0 <= index < len(slots) else None


def next_uncooked_index(chat_id: int) -> int | None:
    state = _load(chat_id)
    if not state:
        return None
    slots = _flat_slots(state)
    return min(state["progress_index"], len(slots) - 1) if slots else None


def current_day_first_index(chat_id: int) -> int | None:
    """Индекс первого слота ещё не сгенерированного (следующего по очереди) дня плана."""
    state = _load(chat_id)
    if not state or state["current_day"] > state["days_total"]:
        return None
    return (state["current_day"] - 1) * len(state["meal_keys"])
