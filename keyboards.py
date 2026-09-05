"""
Inline-клавиатуры для анкеты бота.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def cuisine_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🍛 Аюрведическая", callback_data="cuisine:ayurveda")
    b.button(text="🍲 Классическая", callback_data="cuisine:classic")
    b.adjust(1)
    return b.as_markup()


def skip_kb(step: str) -> InlineKeyboardMarkup:
    """Кнопка «пропустить» для шагов со свободным вводом текста."""
    b = InlineKeyboardBuilder()
    b.button(text="➡️ Пропустить", callback_data=f"skip:{step}")
    return b.as_markup()


def time_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    options = ["15 мин", "30 мин", "45 мин", "60 мин", "Не важно"]
    for opt in options:
        b.button(text=opt, callback_data=f"time:{opt}")
    b.adjust(2, 2, 1)
    return b.as_markup()


def servings_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for n in range(1, 7):
        b.button(text=str(n), callback_data=f"servings:{n}")
    b.adjust(3, 3)
    return b.as_markup()


def appliance_kb(selected: set[str] | None = None) -> InlineKeyboardMarkup:
    """
    Мультивыбор режима приготовления. Отмеченные варианты помечаются ✅.
    selected — множество уже выбранных ключей.
    """
    selected = selected or set()
    options = {
        "airfryer": "Аэрогриль",
        "multicooker": "Мультиварка",
        "oven": "Духовка",
        "stove": "Плита",
    }
    b = InlineKeyboardBuilder()
    for key, label in options.items():
        mark = "✅ " if key in selected else ""
        b.button(text=f"{mark}{label}", callback_data=f"appliance:{key}")
    b.button(text="Готово ➡️", callback_data="appliance:done")
    b.adjust(2, 2, 1)
    return b.as_markup()


def confirm_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔍 Найти рецепт", callback_data="confirm:go")
    b.button(text="🔄 Начать заново", callback_data="confirm:restart")
    b.adjust(1)
    return b.as_markup()


def restart_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🍳 Подобрать ещё рецепт", callback_data="confirm:restart")
    return b.as_markup()
