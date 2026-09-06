"""
Inline-клавиатуры для анкеты бота.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def cuisine_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🍛 Аюрведическая", callback_data="cuisine:ayurveda")
    b.button(text="🍲 Классическая", callback_data="cuisine:classic")
    b.button(text="⭐ Избранное", callback_data="favorites:open")
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


def recipe_result_kb() -> InlineKeyboardMarkup:
    """Кнопки под готовым рецептом: добавить в избранное / новый рецепт."""
    b = InlineKeyboardBuilder()
    b.button(text="⭐ В избранное", callback_data="fav:save")
    b.button(text="🍳 Подобрать ещё рецепт", callback_data="confirm:restart")
    b.adjust(1)
    return b.as_markup()


def favorites_list_kb(favorites: list[dict]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for fav in favorites:
        title = fav["title"]
        short = title if len(title) <= 40 else title[:37] + "..."
        b.button(text=f"🍽 {short}", callback_data=f"fav:view:{fav['id']}")
    b.button(text="🔎 Поиск по избранному", callback_data="favorites:search")
    b.button(text="⬅️ Назад", callback_data="favorites:back")
    b.adjust(1)
    return b.as_markup()


def favorite_view_kb(fav_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🗑 Удалить из избранного", callback_data=f"fav:delete:{fav_id}")
    b.button(text="⬅️ К списку избранного", callback_data="favorites:open")
    b.adjust(1)
    return b.as_markup()
