"""
Inline-клавиатуры для анкеты бота.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from cuisines import CUISINE_LABELS, MACRO_GOAL_KEY

# Текст кнопок постоянного нижнего меню (reply-keyboard) — вынесен в константы, чтобы
# сверяться с ним в обработчиках bot.py без риска опечатки.
BTN_COOK = "🍳 Готовить"
BTN_FAVORITES = "⭐ Избранное"
BTN_DAY_MENU = "📅 Меню дня"


def main_reply_kb() -> ReplyKeyboardMarkup:
    """
    Постоянное меню внизу экрана (не сообщение в чате!) — открывается/прячется одной
    кнопкой-иконкой клавиатуры у поля ввода в Telegram, не засоряя историю чата.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_COOK), KeyboardButton(text=BTN_DAY_MENU), KeyboardButton(text=BTN_FAVORITES)],
        ],
        resize_keyboard=True,
    )


CUISINE_EMOJI = {
    "ayurveda": "🍛",
    "classic": "🍲",
    "balanced": "⚖️",
    "athlete_endurance": "🏃",
    "mass_gain": "💪",
}


def cuisine_kb() -> InlineKeyboardMarkup:
    """
    Стартовое меню сгруппировано по смыслу и разложено по 2 кнопки в ряд, чтобы не
    растягиваться в длинный список: сначала типы питания/кухни, потом альтернативные
    режимы подбора (по КБЖУ / меню на день), потом избранное отдельной строкой.
    """
    b = InlineKeyboardBuilder()
    for key, label in CUISINE_LABELS.items():
        if key == MACRO_GOAL_KEY:
            continue
        emoji = CUISINE_EMOJI.get(key, "🍽")
        b.button(text=f"{emoji} {label}", callback_data=f"cuisine:{key}")
    b.button(text="🎯 Подобрать по КБЖУ", callback_data=f"cuisine:{MACRO_GOAL_KEY}")
    b.button(text="📅 Меню на день", callback_data="daymenu:start")
    b.button(text="⭐ Избранное", callback_data="favorites:open")
    b.adjust(2, 2, 1, 2, 1)
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


MEAL_LABELS = {
    "breakfast": "🍳 Завтрак",
    "brunch": "🥐 Бранч",
    "lunch": "🍲 Обед",
    "snack": "🍎 Перекус",
    "dinner": "🍽 Ужин",
}

# Ориентировочная доля от общей дневной цели по КБЖУ (нормализуется под фактически
# выбранный набор приёмов пищи в day_menu.py).
MEAL_KCAL_SHARE = {
    "breakfast": 0.25,
    "brunch": 0.30,
    "lunch": 0.35,
    "snack": 0.10,
    "dinner": 0.30,
}


def day_menu_missing_kb() -> InlineKeyboardMarkup:
    """Показывается, если у пользователя ещё нет собранного меню на день."""
    b = InlineKeyboardBuilder()
    b.button(text="📅 Собрать меню на день", callback_data="daymenu:start")
    return b.as_markup()


def meals_kb(selected: set[str] | None = None) -> InlineKeyboardMarkup:
    """Мультивыбор приёмов пищи для меню на день."""
    selected = selected or set()
    b = InlineKeyboardBuilder()
    for key, label in MEAL_LABELS.items():
        mark = "✅ " if key in selected else ""
        b.button(text=f"{mark}{label}", callback_data=f"daymeal:{key}")
    b.button(text="Готово ➡️", callback_data="daymeal:done")
    b.adjust(2, 2, 1, 1)
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
