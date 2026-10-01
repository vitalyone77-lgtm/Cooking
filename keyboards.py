"""
Inline-клавиатуры для анкеты бота.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
from cuisines import CUISINE_LABELS, MACRO_GOAL_KEY

# Текст кнопок постоянного нижнего меню (reply-keyboard).
BTN_MENU = "📋 Показать меню"
BTN_READY = "🗂 Готовые меню"
BTN_HELP = "❓ Справка"

# Тексты кнопок ПРОШЛЫХ версий: у людей, у которых старая клавиатура ещё висит в чате,
# нажатие должно работать, а клавиатура — обновиться на новую.
LEGACY_BTN_COOK = "🍳 Готовить"
LEGACY_BTN_FAVORITES = "⭐ Избранное"
LEGACY_BTN_DAY_MENU = "📅 Меню дня"
LEGACY_BTN_WEEK_MENU = "📆 План питания"


def main_reply_kb() -> ReplyKeyboardMarkup:
    """
    Постоянное меню внизу экрана: одна строка из трёх кнопок. is_persistent=True — Telegram
    не прячет её после нажатия и показывает вместо иконки-клавиатуры.
    """
    return ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(text=BTN_MENU),
            KeyboardButton(text=BTN_READY),
            KeyboardButton(text=BTN_HELP),
        ]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Нажми «Показать меню» или напиши, что хочешь приготовить",
    )


CUISINE_EMOJI = {
    "ayurveda": "🍛",
    "classic": "🍲",
    "balanced": "⚖️",
    "athlete_endurance": "🏃",
    "mass_gain": "💪",
}


def cuisine_kb(with_extras: bool = True) -> InlineKeyboardMarkup:
    """
    Главное меню: типы питания (по 2 в ряд), подбор по КБЖУ; при with_extras — ещё меню на
    день/несколько дней, избранное, магазины, справка. Внутри анкет «на день»/«на N дней»
    extras не нужны (with_extras=False), иначе можно случайно прервать анкету.
    """
    b = InlineKeyboardBuilder()
    for key, label in CUISINE_LABELS.items():
        if key == MACRO_GOAL_KEY:
            continue
        emoji = CUISINE_EMOJI.get(key, "🍽")
        b.button(text=f"{emoji} {label}", callback_data=f"cuisine:{key}")
    b.button(text="🎯 Подобрать по КБЖУ", callback_data=f"cuisine:{MACRO_GOAL_KEY}")
    if not with_extras:
        b.adjust(2)
        return b.as_markup()
    b.button(text="📅 Меню на день", callback_data="daymenu:start")
    b.button(text=f"📆 Меню на {config.WEEK_MENU_DAYS} дня", callback_data="weekmenu:start")
    b.button(text="⭐ Избранное", callback_data="favorites:open")
    b.button(text="🏪 Магазины", callback_data="stores:open")
    b.button(text="❓ Справка", callback_data="help:open")
    b.adjust(2, 2, 2, 2, 1, 2)
    return b.as_markup()


def stores_kb(enabled: list[str]) -> InlineKeyboardMarkup:
    from stores.links import STORES
    b = InlineKeyboardBuilder()
    for key, (_, full, _) in STORES.items():
        mark = "✅" if key in enabled else "⬜"
        b.button(text=f"{mark} {full}", callback_data=f"stores:toggle:{key}")
    b.button(text="⬅️ В меню", callback_data="menu:open")
    b.adjust(1)
    return b.as_markup()


def ready_hub_kb(has_day: bool, has_week: bool, week_days: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if has_day:
        b.button(text="📅 Меню на день", callback_data="ready:day")
    if has_week:
        b.button(text=f"📆 План на {week_days} дня", callback_data="ready:week")
    if not has_day:
        b.button(text="➕ Собрать меню на день", callback_data="daymenu:start")
    if not has_week:
        b.button(text=f"➕ Собрать план на {week_days} дня", callback_data="weekmenu:start")
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


def week_menu_missing_kb() -> InlineKeyboardMarkup:
    """Показывается, если у пользователя ещё нет плана питания на несколько дней."""
    b = InlineKeyboardBuilder()
    b.button(text=f"📆 Собрать план на {config.WEEK_MENU_DAYS} дня", callback_data="weekmenu:start")
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
    b.button(text="🛒 Найти продукты в магазине", callback_data="shop:open:r")
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
    b.button(text="🛒 Найти продукты в магазине", callback_data="shop:open:r")
    b.button(text="🗑 Удалить из избранного", callback_data=f"fav:delete:{fav_id}")
    b.button(text="⬅️ К списку избранного", callback_data="favorites:open")
    b.adjust(1)
    return b.as_markup()


def shopping_btn_kb(ctx: str) -> InlineKeyboardMarkup:
    """Одна кнопка «Найти продукты в магазине» (ctx: r — рецепт, d — меню на день, w — корзина плана, wd<N> — докупить к дню N)."""
    b = InlineKeyboardBuilder()
    b.button(text="🛒 Найти продукты в магазине", callback_data=f"shop:open:{ctx}")
    return b.as_markup()


def store_pick_kb(ctx: str, enabled: list[str], current: str | None = None) -> InlineKeyboardMarkup:
    """Выбор магазина для списка продуктов; текущий помечен ✅, «Все магазины» — старый формат со всеми ссылками."""
    from stores.links import STORES
    b = InlineKeyboardBuilder()
    for key in enabled:
        if key in STORES:
            mark = "✅ " if key == current else ""
            b.button(text=f"{mark}{STORES[key][0]}", callback_data=f"shop:s:{ctx}:{key}")
    mark = "✅ " if current == "all" else ""
    b.button(text=f"{mark}Все магазины", callback_data=f"shop:s:{ctx}:all")
    b.adjust(2)
    return b.as_markup()
