"""
Кухонный бот-помощник для мамы 🍲
Анкета (кухня → продукты → исключения → время → порции → техника) →
поиск рецептов в интернете → анализ ИИ → готовый рецепт с продуктами и инструкцией.

Запуск: python bot.py
"""
import asyncio
import logging
from collections import OrderedDict

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, BotCommand

import config
import keyboards as kb
import day_menu
import week_menu
from cuisines import cuisine_label, MACRO_GOAL_KEY
from states import RecipeForm, FavoritesForm, DayMenuForm, WeekMenuForm
from search import search_recipes, format_results_for_prompt
from ai import generate_recipe
from shopping import (
    extract_shopping_terms, extract_dish_title, format_store_links, terms_from_recipe_text,
    amounts_from_recipe_text, merge_amounts,
)
from storage import save_last_request, get_last_request, set_last_recipe
from reminders import setup_scheduler
from favorites import add_favorite, get_favorites, get_favorite, remove_favorite, search_favorites
from last_recipe import set_last_recipe, get_last_recipe
from stores import prefs as store_prefs
from stores.links import DEFAULT_STORES
from stores.basket import build_priced_basket

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

APPLIANCE_LABELS = {
    "airfryer": "Аэрогриль",
    "multicooker": "Мультиварка",
    "oven": "Духовка",
    "stove": "Плита",
}

bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher(storage=MemoryStorage())


# ---------- Вспомогательное ----------

HELP_TEXT = (
    "❓ *Справка*\n\n"
    "Я помогаю решить, что приготовить: подбираю рецепт, считаю калории и продукты, "
    "показываю, где их купить.\n\n"
    "*Внизу три кнопки (они всегда на месте):*\n"
    "📋 *Показать меню* — выбрать тип питания и получить рецепт, собрать меню на день или на "
    f"{config.WEEK_MENU_DAYS} дня, открыть избранное.\n"
    "🗂 *Готовые меню* — открыть уже собранное меню на день и план на несколько дней.\n"
    "❓ *Справка* — этот текст.\n\n"
    "Под рецептом, меню и планом есть кнопка «🛒 Найти продукты в магазине»: выбираешь магазин "
    "(Пятёрочка, ВкусВилл, Перекрёсток, Лавка, Купер) — и получаешь короткий список со ссылками на поиск.\n\n"
    "*Команды:* /menu — меню, /ready — готовые меню,"
    "/app — веб-версия для телефона, /help — справка.\n\n"
    "Каждый день в 14:00 я напоминаю о новом блюде. Если что-то зависло — /start."
)


async def answer_long(message: Message, text: str, reply_markup=None, **kwargs):
    """Отправляет длинный текст частями (лимит Telegram — 4096 символов), разметка — на последней."""
    limit = 3900
    parts, buf = [], ""
    for para in text.split("\n"):
        if len(buf) + len(para) + 1 > limit and buf:
            parts.append(buf)
            buf = ""
        buf += (("\n" if buf else "") + para)[:limit]
    if buf:
        parts.append(buf)
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        markup = reply_markup if last else None
        try:
            await message.answer(part, reply_markup=markup, **kwargs)
        except TelegramBadRequest:
            # Сломалась Markdown-разметка (например, «_» или «*» в названии товара) — шлём без неё
            await message.answer(part, reply_markup=markup, parse_mode=None, **kwargs)


# ---------- Кнопка «🛒 Найти продукты в магазине» ----------
# Список ссылок не показываем сразу (он длинный): под рецептом/меню/планом стоит кнопка → выбор магазина →
# короткий список ссылок только на этот магазин. Продукты берутся не из памяти, а из данных, которые
# переживают перезапуск: для рецепта — из текста сообщения, на которое ответили (с запасом в памяти — точные названия
# из служебной строки ИИ), для меню и плана — из сохранённых файлов.

_recipe_terms: "OrderedDict[tuple[int, int], list[str]]" = OrderedDict()


def remember_recipe_terms(chat_id: int, message_id: int, terms: list[str]) -> None:
    if terms:
        _recipe_terms[(chat_id, message_id)] = terms
        while len(_recipe_terms) > 300:
            _recipe_terms.popitem(last=False)


def resolve_shopping_terms(message: Message, ctx: str) -> list[str]:
    """ctx: r — рецепт (сообщение, на которое ответили), d — меню на день, w — корзина плана, wd<N> — докупить к дню N."""
    chat_id = message.chat.id
    if ctx == "r":
        # кнопка может стоять на самом сообщении с рецептом, а выбор магазина — ответом на него
        for src in (message.reply_to_message, message):
            if not src:
                continue
            terms = _recipe_terms.get((chat_id, src.message_id)) or terms_from_recipe_text(src.text or "")
            if terms:
                return terms
        return []
    if ctx == "d":
        return day_menu.get_shopping_terms(chat_id)
    if ctx == "w":
        info = week_menu.get_basket_info(chat_id)
        return list(info["inventory_initial"].keys()) if info else []
    if ctx.startswith("wd") and ctx[2:].isdigit():
        return week_menu.get_day_shopping_terms(chat_id, int(ctx[2:]))
    return []


async def edit_text_safe(message: Message, text: str, reply_markup=None, **kwargs):
    try:
        await message.edit_text(text, reply_markup=reply_markup, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=None, **kwargs)


@dp.callback_query(F.data.startswith("shop:open:"))
async def shop_open(callback: CallbackQuery):
    ctx = callback.data.split(":", 2)[2]
    if not resolve_shopping_terms(callback.message, ctx):
        await callback.answer("Не нашёл список продуктов — открой рецепт заново.", show_alert=True)
        return
    # Ответом на сообщение с рецептом: так по нему же восстановим продукты на следующем шаге
    await callback.message.reply(
        "🛒 В каком магазине искать продукты?",
        reply_markup=kb.store_pick_kb(ctx, DEFAULT_STORES),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("shop:s:"))
async def shop_store(callback: CallbackQuery):
    _, _, ctx, store = callback.data.split(":", 3)
    chat_id = callback.message.chat.id
    terms = resolve_shopping_terms(callback.message, ctx)
    if not terms:
        await callback.answer("Не нашёл список продуктов — открой рецепт заново.", show_alert=True)
        return
    if store not in DEFAULT_STORES:
        await callback.answer("Неизвестный магазин", show_alert=True)
        return
    text = format_store_links(terms, store, resolve_shopping_amounts(callback.message, ctx))
    await edit_text_safe(
        callback.message, text, reply_markup=kb.store_pick_kb(ctx, DEFAULT_STORES, current=store),
        disable_web_page_preview=True,
    )
    await callback.answer()


def resolve_shopping_amounts(message: Message, ctx: str) -> dict[str, int]:
    """Количества продуктов (граммы, яйца — штуки) для сборки корзины: из рецепта, меню на день или корзины плана."""
    chat_id = message.chat.id
    if ctx == "r":
        for src in (message.reply_to_message, message):
            amounts = amounts_from_recipe_text(src.text or "") if src else {}
            if amounts:
                return amounts
        return {}
    if ctx == "d":
        return day_menu.get_shopping_amounts(chat_id)
    if ctx == "w":
        info = week_menu.get_basket_info(chat_id)
        return dict(info["inventory_initial"]) if info else {}
    return {}


@dp.callback_query(F.data.startswith("shop:cart:"))
async def shop_cart(callback: CallbackQuery):
    """Собирает корзину во ВкусВилле: подбирает товары и упаковки, считает цену, даёт ссылку «открыть корзину»."""
    ctx = callback.data.split(":", 2)[2]
    amounts = resolve_shopping_amounts(callback.message, ctx)
    if not amounts:
        await callback.answer("В рецепте нет продуктов с количеством — собрать корзину не из чего.", show_alert=True)
        return
    await callback.answer()
    scope = {"r": "этот рецепт", "d": "весь день", "w": "весь план"}.get(ctx, "весь план")
    wait = await callback.message.answer("💰 Собираю корзину во ВкусВилле…")
    priced = await build_priced_basket(amounts, scope)
    if priced:
        await wait.delete()
        await answer_long(callback.message, priced, disable_web_page_preview=True)
    else:
        await wait.edit_text("ℹ️ ВкусВилл сейчас не ответил или ничего не нашёл — открой список ссылок и ищи продукты вручную.")


async def show_main_menu(message: Message, state: FSMContext, intro: str = "Что готовим? Выбери тип питания/кухни:"):
    await state.clear()
    await message.answer(intro, reply_markup=kb.cuisine_kb())
    await state.set_state(RecipeForm.cuisine)


async def show_ready_menus(message: Message, state: FSMContext):
    """
    Средняя кнопка нижнего меню: открывает ГОТОВЫЕ меню. Если есть только одно (дневное или
    на несколько дней) — открывает его сразу; если оба — даёт выбор; если ни одного — предлагает собрать.
    """
    await state.clear()
    chat_id = message.chat.id
    has_day = day_menu.has_menu(chat_id)
    has_week = week_menu.has_plan(chat_id)
    if has_day and not has_week:
        text, markup = day_menu.build_summary_view(chat_id)
        await message.answer(text, reply_markup=markup)
    elif has_week and not has_day:
        text, markup = week_menu.build_status_view(chat_id)
        await message.answer(text, reply_markup=markup)
    else:
        title = "🗂 Готовые меню — что открыть?" if has_day else "🗂 Готовых меню пока нет. Собрать?"
        await message.answer(title, reply_markup=kb.ready_hub_kb(has_day, has_week, config.WEEK_MENU_DAYS))


# ---------- Старт ----------

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    # Reply-клавиатура и инлайн-клавиатура — разные типы reply_markup, за один вызов
    # можно прикрепить только один, поэтому нижнее меню отправляем отдельным сообщением.
    await message.answer(
        "Привет! 👋 Я кухонный помощник. Внизу — постоянные кнопки: "
        "«Показать меню», «Готовые меню» и «Справка».",
        reply_markup=kb.main_reply_kb(),
    )
    await show_main_menu(message, state, "Для начала выбери тип питания/кухни:")


@dp.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    await show_main_menu(message, state)


@dp.message(Command("ready"))
async def cmd_ready(message: Message, state: FSMContext):
    await show_ready_menus(message, state)


@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(HELP_TEXT, reply_markup=kb.main_reply_kb())


@dp.message(Command("app"))
async def cmd_app(message: Message):
    if not config.WEB_APP_URL:
        await message.answer("Веб-версия пока не настроена (нужен WEB_APP_URL в .env).")
        return
    await message.answer(
        "🌐 Веб-версия работает отдельно от Telegram, регистрация не нужна:\n"
        f"{config.WEB_APP_URL}\n\nОткрой на телефоне и добавь на главный экран."
    )


@dp.callback_query(F.data == "confirm:restart")
async def restart(callback: CallbackQuery, state: FSMContext):
    await show_main_menu(callback.message, state, "Хорошо, начнём заново! Выбери тип питания/кухни:")
    await callback.answer()


@dp.callback_query(F.data == "menu:open")
async def menu_open(callback: CallbackQuery, state: FSMContext):
    await show_main_menu(callback.message, state)
    await callback.answer()


@dp.callback_query(F.data == "help:open")
async def help_open(callback: CallbackQuery):
    await callback.message.answer(HELP_TEXT)
    await callback.answer()


# ---------- Постоянное нижнее меню (reply-keyboard) ----------
# Зарегистрированы раньше остальных message-хэндлеров, чтобы иметь приоритет: даже если
# пользователь сейчас в середине анкеты, нажатие кнопки нижнего меню перехватывает управление.

@dp.message(F.text == kb.BTN_MENU)
async def menu_btn_menu(message: Message, state: FSMContext):
    await show_main_menu(message, state)


@dp.message(F.text == kb.BTN_READY)
async def menu_btn_ready(message: Message, state: FSMContext):
    await show_ready_menus(message, state)


@dp.message(F.text == kb.BTN_HELP)
async def menu_btn_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(HELP_TEXT)


# Кнопки старых версий: клавиатура могла остаться у пользователя в чате. Обрабатываем и
# заодно подставляем новую клавиатуру.
@dp.message(F.text.in_({kb.LEGACY_BTN_COOK, kb.LEGACY_BTN_FAVORITES, kb.LEGACY_BTN_DAY_MENU, kb.LEGACY_BTN_WEEK_MENU}))
async def legacy_buttons(message: Message, state: FSMContext):
    await message.answer("Обновил нижнее меню ⌨️", reply_markup=kb.main_reply_kb())
    if message.text == kb.LEGACY_BTN_FAVORITES:
        favs = get_favorites(message.chat.id)
        if favs:
            await state.clear()
            await message.answer(f"⭐ Твоё избранное ({len(favs)}):", reply_markup=kb.favorites_list_kb(favs))
            return
    if message.text in (kb.LEGACY_BTN_DAY_MENU, kb.LEGACY_BTN_WEEK_MENU):
        await show_ready_menus(message, state)
        return
    await show_main_menu(message, state)


@dp.callback_query(F.data == "ready:day")
async def ready_day(callback: CallbackQuery):
    view = day_menu.build_summary_view(callback.message.chat.id)
    if not view:
        await callback.answer("Меню на день не найдено.", show_alert=True)
        return
    text, markup = view
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "ready:week")
async def ready_week(callback: CallbackQuery):
    view = week_menu.build_status_view(callback.message.chat.id)
    if not view:
        await callback.answer("План не найден.", show_alert=True)
        return
    text, markup = view
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


# ---------- Шаг 1: кухня ----------

@dp.callback_query(RecipeForm.cuisine, F.data.startswith("cuisine:"))
async def step_cuisine(callback: CallbackQuery, state: FSMContext):
    cuisine = callback.data.split(":", 1)[1]
    await state.update_data(cuisine=cuisine)
    await callback.message.edit_reply_markup()

    if cuisine == MACRO_GOAL_KEY:
        await callback.message.answer(
            "Укажи цель по КБЖУ на этот приём пищи, например:\n"
            "«500 ккал, белки 40 г» или «около 600 ккал, много белка, поменьше углеводов».\n"
            "Если без разницы — нажми «Пропустить», подберу сбалансированный вариант.",
            reply_markup=kb.skip_kb("macro_goal"),
        )
        await state.set_state(RecipeForm.macro_goal)
    else:
        await ask_preferred(callback.message, state)
    await callback.answer()


# ---------- Шаг 1.5 (только ветка «Подобрать по КБЖУ»): цель по КБЖУ ----------

@dp.message(RecipeForm.macro_goal)
async def step_macro_goal_text(message: Message, state: FSMContext):
    await state.update_data(macro_goal=message.text.strip())
    await ask_preferred(message, state)


@dp.callback_query(RecipeForm.macro_goal, F.data == "skip:macro_goal")
async def step_macro_goal_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(macro_goal="")
    await callback.message.edit_reply_markup()
    await ask_preferred(callback.message, state)
    await callback.answer()


async def ask_preferred(message: Message, state: FSMContext):
    await message.answer(
        "Отлично! Теперь напиши, какие продукты ты хочешь использовать "
        "(например: курица, рис, брокколи) — или сразу название блюда, "
        "которое хочешь приготовить (например: «хочу плов» или «хочу борщ»).\n"
        "Если без разницы — нажми «Пропустить».",
        reply_markup=kb.skip_kb("preferred"),
    )
    await state.set_state(RecipeForm.preferred)


# ---------- Шаг 2: предпочитаемые продукты ----------

@dp.message(RecipeForm.preferred)
async def step_preferred_text(message: Message, state: FSMContext):
    await state.update_data(preferred=message.text.strip())
    await ask_excluded(message, state)


@dp.callback_query(RecipeForm.preferred, F.data == "skip:preferred")
async def step_preferred_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(preferred="")
    await callback.message.edit_reply_markup()
    await ask_excluded(callback.message, state)
    await callback.answer()


async def ask_excluded(message: Message, state: FSMContext):
    await message.answer(
        "Какие продукты нужно исключить? (аллергии, нелюбимые продукты)\n"
        "Например: орехи, грибы, лук.\n"
        "Если исключать нечего — нажми «Пропустить».",
        reply_markup=kb.skip_kb("excluded"),
    )
    await state.set_state(RecipeForm.excluded)


# ---------- Шаг 3: исключения ----------

@dp.message(RecipeForm.excluded)
async def step_excluded_text(message: Message, state: FSMContext):
    await state.update_data(excluded=message.text.strip())
    await ask_time(message, state)


@dp.callback_query(RecipeForm.excluded, F.data == "skip:excluded")
async def step_excluded_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(excluded="")
    await callback.message.edit_reply_markup()
    await ask_time(callback.message, state)
    await callback.answer()


async def ask_time(message: Message, state: FSMContext):
    await message.answer("Сколько времени есть на готовку?", reply_markup=kb.time_kb())
    await state.set_state(RecipeForm.time)


# ---------- Шаг 4: время ----------

@dp.callback_query(RecipeForm.time, F.data.startswith("time:"))
async def step_time(callback: CallbackQuery, state: FSMContext):
    time_value = callback.data.split(":", 1)[1]
    await state.update_data(time=time_value)
    await callback.message.edit_reply_markup()
    await callback.message.answer("На сколько порций готовим?", reply_markup=kb.servings_kb())
    await state.set_state(RecipeForm.servings)
    await callback.answer()


# ---------- Шаг 5: порции ----------

@dp.callback_query(RecipeForm.servings, F.data.startswith("servings:"))
async def step_servings(callback: CallbackQuery, state: FSMContext):
    servings = int(callback.data.split(":", 1)[1])
    await state.update_data(servings=servings, appliance=[])
    await callback.message.edit_reply_markup()
    await callback.message.answer(
        "В чём будем готовить? Можно выбрать несколько вариантов.",
        reply_markup=kb.appliance_kb(set()),
    )
    await state.set_state(RecipeForm.appliance)
    await callback.answer()


# ---------- Шаг 6: техника (мультивыбор) ----------

@dp.callback_query(RecipeForm.appliance, F.data.startswith("appliance:"))
async def step_appliance(callback: CallbackQuery, state: FSMContext):
    value = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected = set(data.get("appliance", []))

    if value == "done":
        if not selected:
            await callback.answer("Выбери хотя бы один вариант!", show_alert=True)
            return
        labels = [APPLIANCE_LABELS[k] for k in selected]
        await state.update_data(appliance_labels=labels)
        await callback.message.edit_reply_markup()
        await show_summary(callback.message, state)
        await callback.answer()
        return

    if value in selected:
        selected.remove(value)
    else:
        selected.add(value)
    await state.update_data(appliance=list(selected))
    await callback.message.edit_reply_markup(reply_markup=kb.appliance_kb(selected))
    await callback.answer()


# ---------- Подтверждение ----------

async def show_summary(message: Message, state: FSMContext):
    data = await state.get_data()
    macro_goal = data.get("macro_goal")
    text = (
        "📋 Проверим запрос:\n\n"
        f"Тип питания: {cuisine_label(data.get('cuisine'))}\n"
        + (f"Цель по КБЖУ: {macro_goal}\n" if macro_goal else "")
        + f"Предпочитаемые продукты: {data.get('preferred') or '—'}\n"
        f"Исключить: {data.get('excluded') or '—'}\n"
        f"Время: {data.get('time')}\n"
        f"Порций: {data.get('servings')}\n"
        f"Техника: {', '.join(data.get('appliance_labels', []))}\n"
    )
    await message.answer(text, reply_markup=kb.confirm_kb())
    await state.set_state(RecipeForm.confirm)


@dp.callback_query(RecipeForm.confirm, F.data == "confirm:go")
async def step_confirm_go(callback: CallbackQuery, state: FSMContext):
    await callback.answer()  # отвечаем Telegram сразу, не дожидаясь долгого поиска+ИИ
    await callback.message.edit_reply_markup()
    status_msg = await callback.message.answer("🔎 Ищу рецепты в интернете...")
    data = await state.get_data()

    try:
        search_query, results = await search_recipes(data)
        await status_msg.edit_text(
            f"🔎 Искал по запросу: «{search_query}»\n🤖 Анализирую и подбираю рецепт..."
        )

        results_text = format_results_for_prompt(results)
        recipe_text = await generate_recipe(data, search_query, results_text)
        recipe_text, shopping_terms = extract_shopping_terms(recipe_text)

        if not recipe_text.strip():
            await status_msg.edit_text(
                "😔 Не получилось составить рецепт под эти параметры (возможно, слишком "
                "противоречивые условия, например цель по КБЖУ). Попробуй изменить запрос "
                "и повторить.",
                reply_markup=kb.restart_kb(),
            )
            return

        dish_title = extract_dish_title(recipe_text)
        save_last_request(callback.message.chat.id, data, dish_title)

        set_last_recipe(callback.message.chat.id, dish_title, recipe_text, data.get("cuisine"))
    except Exception as e:
        logger.exception("Ошибка при подборе рецепта")
        await status_msg.edit_text(
            f"😔 Что-то пошло не так при подборе рецепта.\nТехническая причина: {e}\n\n"
            "Попробуй ещё раз.",
            reply_markup=kb.restart_kb(),
        )
        return

    await status_msg.delete()
    # Рецепт и кнопки («Найти продукты в магазине», «В избранное», «Ещё рецепт») — одним сообщением
    sent = await callback.message.answer(recipe_text, reply_markup=kb.recipe_result_kb())
    remember_recipe_terms(callback.message.chat.id, sent.message_id, shopping_terms)



# ---------- Напоминание: кнопки в рассылке ----------

@dp.callback_query(F.data == "reminder:ok")
async def reminder_ok(callback: CallbackQuery):
    """Нажатие кнопки «Приготовить» в напоминании — просто убираем кнопки."""
    await callback.message.edit_reply_markup()
    await callback.answer()


@dp.callback_query(F.data == "reminder:next")
async def reminder_next(callback: CallbackQuery, state: FSMContext):
    """Нажатие кнопки «Найти другой рецепт» — генерируем новый рецепт."""
    chat_id = callback.message.chat.id
    
    # Получаем сохранённые параметры
    data = get_last_request(chat_id)
    if not data:
        await callback.answer("Параметры не найдены, попробуй /menu", show_alert=True)
        return
    
    await callback.answer()
    await callback.message.edit_reply_markup()
    
    # Генерируем новый рецепт
    status_msg = await callback.message.answer("🔎 Ищу рецепты...")
    
    try:
        search_query, results = await search_recipes(data)
        await status_msg.edit_text(f"🤖 Подбираю рецепт...")
        
        results_text = format_results_for_prompt(results)
        recipe_text = await generate_recipe(data, search_query, results_text)
        recipe_text, shopping_terms = extract_shopping_terms(recipe_text)
        
        if not recipe_text.strip():
            await status_msg.edit_text("Не удалось составить рецепт, попробуй позже.")
            return
        
        dish_title = extract_dish_title(recipe_text)
        set_last_recipe(chat_id, dish_title, recipe_text, data.get("cuisine"))
        
        await status_msg.delete()
        sent = await callback.message.answer(recipe_text, reply_markup=kb.recipe_result_kb())
        remember_recipe_terms(chat_id, sent.message_id, shopping_terms)
        
    except Exception as e:
        logger.exception("Ошибка при подборе рецепта в напоминании")
        await status_msg.edit_text(f"Ошибка: {e}\n\nПопробуй позже.")


# ---------- Меню на день ----------

@dp.callback_query(F.data == "daymenu:start")
async def daymenu_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup()
    await callback.message.answer(
        "📅 Соберём меню на день! Выбери приёмы пищи (можно несколько), потом «Готово»:",
        reply_markup=kb.meals_kb(set()),
    )
    await state.set_state(DayMenuForm.meals)
    await callback.answer()


@dp.callback_query(DayMenuForm.meals, F.data.startswith("daymeal:"))
async def dm_step_meals(callback: CallbackQuery, state: FSMContext):
    value = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected = set(data.get("meals", []))

    if value == "done":
        if not selected:
            await callback.answer("Выбери хотя бы один приём пищи!", show_alert=True)
            return
        await state.update_data(meals=list(selected))
        await callback.message.edit_reply_markup()
        await callback.message.answer("Теперь выбери тип питания/кухни:", reply_markup=kb.cuisine_kb(with_extras=False))
        await state.set_state(DayMenuForm.cuisine)
        await callback.answer()
        return

    if value in selected:
        selected.remove(value)
    else:
        selected.add(value)
    await state.update_data(meals=list(selected))
    await callback.message.edit_reply_markup(reply_markup=kb.meals_kb(selected))
    await callback.answer()


@dp.callback_query(DayMenuForm.cuisine, F.data.startswith("cuisine:"))
async def dm_step_cuisine(callback: CallbackQuery, state: FSMContext):
    cuisine = callback.data.split(":", 1)[1]
    await state.update_data(cuisine=cuisine)
    await callback.message.edit_reply_markup()

    if cuisine == MACRO_GOAL_KEY:
        await callback.message.answer(
            "Укажи ОБЩУЮ цель по КБЖУ на весь день, например:\n"
            "«1800 ккал» или «2000 ккал, белки 120 г».\n"
            "Раздам её по приёмам пищи пропорционально.\n"
            "Если без разницы — нажми «Пропустить».",
            reply_markup=kb.skip_kb("dm_macro_goal"),
        )
        await state.set_state(DayMenuForm.macro_goal)
    else:
        await dm_ask_preferred(callback.message, state)
    await callback.answer()


@dp.message(DayMenuForm.macro_goal)
async def dm_step_macro_goal_text(message: Message, state: FSMContext):
    await state.update_data(macro_goal=message.text.strip())
    await dm_ask_preferred(message, state)


@dp.callback_query(DayMenuForm.macro_goal, F.data == "skip:dm_macro_goal")
async def dm_step_macro_goal_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(macro_goal="")
    await callback.message.edit_reply_markup()
    await dm_ask_preferred(callback.message, state)
    await callback.answer()


async def dm_ask_preferred(message: Message, state: FSMContext):
    await message.answer(
        "Какие продукты хочешь использовать на весь день, в общих чертах "
        "(например: курица, рис, овощи)? Если без разницы — «Пропустить».",
        reply_markup=kb.skip_kb("dm_preferred"),
    )
    await state.set_state(DayMenuForm.preferred)


@dp.message(DayMenuForm.preferred)
async def dm_step_preferred_text(message: Message, state: FSMContext):
    await state.update_data(preferred=message.text.strip())
    await dm_ask_excluded(message, state)


@dp.callback_query(DayMenuForm.preferred, F.data == "skip:dm_preferred")
async def dm_step_preferred_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(preferred="")
    await callback.message.edit_reply_markup()
    await dm_ask_excluded(callback.message, state)
    await callback.answer()


async def dm_ask_excluded(message: Message, state: FSMContext):
    await message.answer(
        "Какие продукты исключить на весь день? (аллергии, нелюбимые продукты)\n"
        "Если исключать нечего — нажми «Пропустить».",
        reply_markup=kb.skip_kb("dm_excluded"),
    )
    await state.set_state(DayMenuForm.excluded)


@dp.message(DayMenuForm.excluded)
async def dm_step_excluded_text(message: Message, state: FSMContext):
    await state.update_data(excluded=message.text.strip())
    await dm_ask_time(message, state)


@dp.callback_query(DayMenuForm.excluded, F.data == "skip:dm_excluded")
async def dm_step_excluded_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(excluded="")
    await callback.message.edit_reply_markup()
    await dm_ask_time(callback.message, state)
    await callback.answer()


async def dm_ask_time(message: Message, state: FSMContext):
    await message.answer("Сколько времени готов тратить на КАЖДОЕ блюдо?", reply_markup=kb.time_kb())
    await state.set_state(DayMenuForm.time)


@dp.callback_query(DayMenuForm.time, F.data.startswith("time:"))
async def dm_step_time(callback: CallbackQuery, state: FSMContext):
    time_value = callback.data.split(":", 1)[1]
    await state.update_data(time=time_value)
    await callback.message.edit_reply_markup()
    await callback.message.answer("На сколько человек готовим?", reply_markup=kb.servings_kb())
    await state.set_state(DayMenuForm.servings)
    await callback.answer()


@dp.callback_query(DayMenuForm.servings, F.data.startswith("servings:"))
async def dm_step_servings(callback: CallbackQuery, state: FSMContext):
    servings = int(callback.data.split(":", 1)[1])
    await state.update_data(servings=servings, appliance=[])
    await callback.message.edit_reply_markup()
    await callback.message.answer(
        "В чём будем готовить? Можно выбрать несколько вариантов.",
        reply_markup=kb.appliance_kb(set()),
    )
    await state.set_state(DayMenuForm.appliance)
    await callback.answer()


@dp.callback_query(DayMenuForm.appliance, F.data.startswith("appliance:"))
async def dm_step_appliance(callback: CallbackQuery, state: FSMContext):
    value = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected = set(data.get("appliance", []))

    if value == "done":
        if not selected:
            await callback.answer("Выбери хотя бы один вариант!", show_alert=True)
            return
        labels = [APPLIANCE_LABELS[k] for k in selected]
        await state.update_data(appliance_labels=labels)
        await callback.message.edit_reply_markup()
        await dm_show_summary(callback.message, state)
        await callback.answer()
        return

    if value in selected:
        selected.remove(value)
    else:
        selected.add(value)
    await state.update_data(appliance=list(selected))
    await callback.message.edit_reply_markup(reply_markup=kb.appliance_kb(selected))
    await callback.answer()


async def dm_show_summary(message: Message, state: FSMContext):
    data = await state.get_data()
    meals = data.get("meals", [])
    meals_text = ", ".join(kb.MEAL_LABELS[m] for m in meals)
    macro_goal = data.get("macro_goal")
    text = (
        "📋 Проверим меню на день:\n\n"
        f"Приёмы пищи: {meals_text}\n"
        f"Тип питания: {cuisine_label(data.get('cuisine'))}\n"
        + (f"Цель по КБЖУ (на весь день): {macro_goal}\n" if macro_goal else "")
        + f"Предпочитаемые продукты: {data.get('preferred') or '—'}\n"
        f"Исключить: {data.get('excluded') or '—'}\n"
        f"Время на каждое блюдо: {data.get('time')}\n"
        f"Порций (на человека): {data.get('servings')}\n"
        f"Техника: {', '.join(data.get('appliance_labels', []))}\n"
    )
    await message.answer(text, reply_markup=kb.confirm_kb())
    await state.set_state(DayMenuForm.confirm)


@dp.callback_query(DayMenuForm.confirm, F.data == "confirm:go")
async def dm_step_confirm_go(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_reply_markup()
    status_msg = await callback.message.answer(
        "🔎 Собираю меню на день — это займёт немного больше времени, чем один рецепт..."
    )
    data = await state.get_data()
    meals = data.get("meals", [])

    try:
        summary = await day_menu.generate_day_menu(callback.message.chat.id, meals, data)
    except Exception as e:
        logger.exception("Ошибка при сборке меню на день")
        await status_msg.edit_text(
            f"😔 Что-то пошло не так при сборке меню на день.\nТехническая причина: {e}\n\n"
            "Попробуй ещё раз.",
            reply_markup=kb.restart_kb(),
        )
        return

    await status_msg.delete()

    # Список продуктов — по кнопке «🛒 Найти продукты в магазине» в самом меню (day_menu.build_summary_view)
    text, markup = day_menu.build_summary_view(callback.message.chat.id)
    await callback.message.answer(text, reply_markup=markup)


@dp.callback_query(F.data.startswith("daymenu:show:"))
async def daymenu_show(callback: CallbackQuery):
    """Разворачивает рецепт блюда ПРЯМО В ЭТОМ ЖЕ сообщении (без нового сообщения в чат)."""
    meal_key = callback.data.split(":", 2)[2]
    view = day_menu.build_meal_view(callback.message.chat.id, meal_key)
    if not view:
        await callback.answer("Рецепт не найден, собери меню заново.", show_alert=True)
        return
    text, markup = view
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "daymenu:collapse")
async def daymenu_collapse(callback: CallbackQuery):
    """Сворачивает рецепт обратно в список блюд — тоже редактированием того же сообщения."""
    view = day_menu.build_summary_view(callback.message.chat.id)
    if not view:
        await callback.answer("Меню не найдено, собери заново.", show_alert=True)
        return
    text, markup = view
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


# ---------- Меню на несколько дней (пилот на config.WEEK_MENU_DAYS дня) ----------

@dp.callback_query(F.data == "weekmenu:start")
async def weekmenu_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup()
    await callback.message.answer(
        f"📆 Соберём план питания на {config.WEEK_MENU_DAYS} дня! "
        "Выбери приёмы пищи (можно несколько), потом «Готово»:",
        reply_markup=kb.meals_kb(set()),
    )
    await state.set_state(WeekMenuForm.meals)
    await callback.answer()


@dp.callback_query(WeekMenuForm.meals, F.data.startswith("daymeal:"))
async def wm_step_meals(callback: CallbackQuery, state: FSMContext):
    value = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected = set(data.get("meals", []))

    if value == "done":
        if not selected:
            await callback.answer("Выбери хотя бы один приём пищи!", show_alert=True)
            return
        await state.update_data(meals=list(selected))
        await callback.message.edit_reply_markup()
        await callback.message.answer("Теперь выбери тип питания/кухни:", reply_markup=kb.cuisine_kb(with_extras=False))
        await state.set_state(WeekMenuForm.cuisine)
        await callback.answer()
        return

    if value in selected:
        selected.remove(value)
    else:
        selected.add(value)
    await state.update_data(meals=list(selected))
    await callback.message.edit_reply_markup(reply_markup=kb.meals_kb(selected))
    await callback.answer()


@dp.callback_query(WeekMenuForm.cuisine, F.data.startswith("cuisine:"))
async def wm_step_cuisine(callback: CallbackQuery, state: FSMContext):
    cuisine = callback.data.split(":", 1)[1]
    await state.update_data(cuisine=cuisine)
    await callback.message.edit_reply_markup()

    if cuisine == MACRO_GOAL_KEY:
        await callback.message.answer(
            "Укажи ОБЩУЮ цель по КБЖУ на КАЖДЫЙ день плана, например:\n"
            "«1800 ккал» или «2000 ккал, белки 120 г».\n"
            "Если без разницы — нажми «Пропустить».",
            reply_markup=kb.skip_kb("wm_macro_goal"),
        )
        await state.set_state(WeekMenuForm.macro_goal)
    else:
        await wm_ask_preferred(callback.message, state)
    await callback.answer()


@dp.message(WeekMenuForm.macro_goal)
async def wm_step_macro_goal_text(message: Message, state: FSMContext):
    await state.update_data(macro_goal=message.text.strip())
    await wm_ask_preferred(message, state)


@dp.callback_query(WeekMenuForm.macro_goal, F.data == "skip:wm_macro_goal")
async def wm_step_macro_goal_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(macro_goal="")
    await callback.message.edit_reply_markup()
    await wm_ask_preferred(callback.message, state)
    await callback.answer()


async def wm_ask_preferred(message: Message, state: FSMContext):
    await message.answer(
        "Какие продукты хочешь использовать на весь период, в общих чертах "
        "(например: курица, рис, овощи)? Если без разницы — «Пропустить».",
        reply_markup=kb.skip_kb("wm_preferred"),
    )
    await state.set_state(WeekMenuForm.preferred)


@dp.message(WeekMenuForm.preferred)
async def wm_step_preferred_text(message: Message, state: FSMContext):
    await state.update_data(preferred=message.text.strip())
    await wm_ask_excluded(message, state)


@dp.callback_query(WeekMenuForm.preferred, F.data == "skip:wm_preferred")
async def wm_step_preferred_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(preferred="")
    await callback.message.edit_reply_markup()
    await wm_ask_excluded(callback.message, state)
    await callback.answer()


async def wm_ask_excluded(message: Message, state: FSMContext):
    await message.answer(
        "Какие продукты исключить на весь период? (аллергии, нелюбимые продукты)\n"
        "Если исключать нечего — нажми «Пропустить».",
        reply_markup=kb.skip_kb("wm_excluded"),
    )
    await state.set_state(WeekMenuForm.excluded)


@dp.message(WeekMenuForm.excluded)
async def wm_step_excluded_text(message: Message, state: FSMContext):
    await state.update_data(excluded=message.text.strip())
    await wm_ask_time(message, state)


@dp.callback_query(WeekMenuForm.excluded, F.data == "skip:wm_excluded")
async def wm_step_excluded_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(excluded="")
    await callback.message.edit_reply_markup()
    await wm_ask_time(callback.message, state)
    await callback.answer()


async def wm_ask_time(message: Message, state: FSMContext):
    await message.answer("Сколько времени готов тратить на КАЖДОЕ блюдо?", reply_markup=kb.time_kb())
    await state.set_state(WeekMenuForm.time)


@dp.callback_query(WeekMenuForm.time, F.data.startswith("time:"))
async def wm_step_time(callback: CallbackQuery, state: FSMContext):
    time_value = callback.data.split(":", 1)[1]
    await state.update_data(time=time_value)
    await callback.message.edit_reply_markup()
    await callback.message.answer("На сколько человек готовим (порций на приём пищи)?", reply_markup=kb.servings_kb())
    await state.set_state(WeekMenuForm.servings)
    await callback.answer()


@dp.callback_query(WeekMenuForm.servings, F.data.startswith("servings:"))
async def wm_step_servings(callback: CallbackQuery, state: FSMContext):
    servings = int(callback.data.split(":", 1)[1])
    await state.update_data(servings=servings, appliance=[])
    await callback.message.edit_reply_markup()
    await callback.message.answer(
        "В чём будем готовить? Можно выбрать несколько вариантов.",
        reply_markup=kb.appliance_kb(set()),
    )
    await state.set_state(WeekMenuForm.appliance)
    await callback.answer()


@dp.callback_query(WeekMenuForm.appliance, F.data.startswith("appliance:"))
async def wm_step_appliance(callback: CallbackQuery, state: FSMContext):
    value = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected = set(data.get("appliance", []))

    if value == "done":
        if not selected:
            await callback.answer("Выбери хотя бы один вариант!", show_alert=True)
            return
        labels = [APPLIANCE_LABELS[k] for k in selected]
        await state.update_data(appliance_labels=labels)
        await callback.message.edit_reply_markup()
        await wm_show_summary(callback.message, state)
        await callback.answer()
        return

    if value in selected:
        selected.remove(value)
    else:
        selected.add(value)
    await state.update_data(appliance=list(selected))
    await callback.message.edit_reply_markup(reply_markup=kb.appliance_kb(selected))
    await callback.answer()


async def wm_show_summary(message: Message, state: FSMContext):
    data = await state.get_data()
    meals = data.get("meals", [])
    meals_text = ", ".join(kb.MEAL_LABELS[m] for m in meals)
    macro_goal = data.get("macro_goal")
    text = (
        f"📋 Проверим план на {config.WEEK_MENU_DAYS} дня:\n\n"
        f"Приёмы пищи (каждый день): {meals_text}\n"
        f"Тип питания: {cuisine_label(data.get('cuisine'))}\n"
        + (f"Цель по КБЖУ (на каждый день): {macro_goal}\n" if macro_goal else "")
        + f"Предпочитаемые продукты: {data.get('preferred') or '—'}\n"
        f"Исключить: {data.get('excluded') or '—'}\n"
        f"Время на каждое блюдо: {data.get('time')}\n"
        f"Порций: {data.get('servings')}\n"
        f"Техника: {', '.join(data.get('appliance_labels', []))}\n"
    )
    await message.answer(text, reply_markup=kb.confirm_kb())
    await state.set_state(WeekMenuForm.confirm)


@dp.callback_query(WeekMenuForm.confirm, F.data == "confirm:go")
async def wm_step_confirm_go(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_reply_markup()
    status_msg = await callback.message.answer(
        "🔎 Считаю продуктовую корзину на весь период..."
    )
    data = await state.get_data()
    meals = data.get("meals", [])
    chat_id = callback.message.chat.id

    try:
        basket_text, _links = await week_menu.start_week_plan(
            chat_id, meals, config.WEEK_MENU_DAYS, data
        )
    except Exception as e:
        logger.exception("Ошибка при сборке продуктовой корзины плана питания")
        await status_msg.edit_text(
            f"😔 Не получилось составить план питания.\nТехническая причина: {e}\n\n"
            "Попробуй ещё раз.",
            reply_markup=kb.restart_kb(),
        )
        return

    await status_msg.delete()
    await answer_long(
        callback.message, basket_text, reply_markup=kb.shopping_btn_kb("w"), disable_web_page_preview=True
    )

    info = week_menu.get_basket_info(chat_id)
    if info and config.VKUSVILL_PRICES_ENABLED:
        price_msg = await callback.message.answer("💰 Считаю стоимость корзины во ВкусВилле...")
        priced = await build_priced_basket(info["inventory_initial"])
        if priced:
            week_menu.save_priced_basket(chat_id, priced)
            await price_msg.delete()
            await answer_long(callback.message, priced, disable_web_page_preview=True)
        else:
            await price_msg.edit_text(
                "ℹ️ Цены ВкусВилла сейчас недоступны — воспользуйся кнопкой «🛒 Найти продукты в магазине» выше."
            )

    text, markup = week_menu.build_status_view(chat_id)
    await callback.message.answer(text, reply_markup=markup)


async def _maybe_send_day_shopping(callback: CallbackQuery, slot: dict):
    """Если день только что сгенерирован и в нём есть продукты сверх основной корзины — присылаем ссылки на докупку."""
    terms = slot.get("shopping_terms") or []
    if slot.get("just_generated") and terms:
        await callback.message.answer(
            f"➕ К дню {slot['day_number']} нужно докупить ещё {len(terms)} продукт(ов) сверх общей корзины.",
            reply_markup=kb.shopping_btn_kb(f"wd{slot['day_number']}"),
        )


@dp.callback_query(F.data.startswith("weekmenu:open:"))
async def weekmenu_open_day(callback: CallbackQuery):
    """Раскрывает/сворачивает день плана (0 — свернуть всё) правкой того же сообщения."""
    day = int(callback.data.split(":", 2)[2])
    view = week_menu.build_status_view(callback.message.chat.id, open_day=day)
    if not view:
        await callback.answer("План не найден.", show_alert=True)
        return
    text, markup = view
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@dp.callback_query(F.data == "weekmenu:noop")
async def weekmenu_noop(callback: CallbackQuery):
    await callback.answer("Сначала приготовь предыдущий день 🙂")


@dp.callback_query(F.data == "weekmenu:basket")
async def weekmenu_basket(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    info = week_menu.get_basket_info(chat_id)
    if not info:
        await callback.answer("План не найден.", show_alert=True)
        return
    await callback.answer()
    if info["basket_text"]:
        await answer_long(
            callback.message, info["basket_text"], reply_markup=kb.shopping_btn_kb("w"), disable_web_page_preview=True
        )
    else:
        await callback.message.answer("🛒 Корзина плана:", reply_markup=kb.shopping_btn_kb("w"))
    if info["priced"]:
        await answer_long(callback.message, info["priced"], disable_web_page_preview=True)


@dp.callback_query(F.data == "weekmenu:cook_next")
async def weekmenu_cook_next(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    index = week_menu.current_day_first_index(chat_id)
    if index is None:
        await callback.answer("План уже завершён или не найден.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text("🔎 Готовлю блюда на сегодня — это займёт чуть больше времени...")

    try:
        slot = await week_menu.ensure_slot(chat_id, index)
    except Exception as e:
        logger.exception("Ошибка при генерации дня плана питания")
        await callback.message.edit_text(
            f"😔 Не получилось приготовить день.\nТехническая причина: {e}\n\nПопробуй ещё раз.",
        )
        return

    text, markup = week_menu.build_slot_view(chat_id, slot)
    await callback.message.edit_text(text, reply_markup=markup)
    await _maybe_send_day_shopping(callback, slot)


@dp.callback_query(F.data.startswith("weekmenu:slot:"))
async def weekmenu_slot(callback: CallbackQuery):
    index = int(callback.data.split(":", 2)[2])
    try:
        slot = await week_menu.ensure_slot(callback.message.chat.id, index)
    except Exception as e:
        await callback.answer(f"Не получилось открыть: {e}", show_alert=True)
        return
    text, markup = week_menu.build_slot_view(callback.message.chat.id, slot)
    await callback.message.edit_text(text, reply_markup=markup)
    await _maybe_send_day_shopping(callback, slot)
    await callback.answer()


@dp.callback_query(F.data.startswith("weekmenu:done:"))
async def weekmenu_done(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    index = int(callback.data.split(":", 2)[2])
    week_menu.mark_done(chat_id, index)
    await callback.answer()

    try:
        slot = await week_menu.ensure_slot(chat_id, index + 1)
    except ValueError:
        view = week_menu.build_status_view(chat_id)
        if view:
            text, markup = view
            await callback.message.edit_text("🎉 Весь план питания приготовлен!\n\n" + text, reply_markup=markup)
        return
    except Exception as e:
        logger.exception("Ошибка при генерации следующего дня плана питания")
        await callback.message.edit_text(f"😔 Не получилось сгенерировать следующий день.\nПричина: {e}")
        return

    text, markup = week_menu.build_slot_view(chat_id, slot)
    await callback.message.edit_text(text, reply_markup=markup)
    await _maybe_send_day_shopping(callback, slot)


@dp.callback_query(F.data == "weekmenu:status")
async def weekmenu_status(callback: CallbackQuery):
    view = week_menu.build_status_view(callback.message.chat.id)
    if not view:
        await callback.answer("План не найден.", show_alert=True)
        return
    text, markup = view
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


# ---------- Избранное ----------

@dp.callback_query(F.data == "favorites:open")
async def favorites_open(callback: CallbackQuery, state: FSMContext):
    chat_id = callback.message.chat.id
    favs = get_favorites(chat_id)
    if favs:
        await callback.message.answer(
            f"⭐ Твоё избранное ({len(favs)}):", reply_markup=kb.favorites_list_kb(favs)
        )
    else:
        await callback.message.answer(
            "Пока в избранном пусто. Понравившийся рецепт можно сохранить кнопкой "
            "«⭐ В избранное» после его получения.",
            reply_markup=kb.cuisine_kb(),
        )
        await state.set_state(RecipeForm.cuisine)
    await callback.answer()


@dp.callback_query(F.data == "favorites:back")
async def favorites_back(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Выбери тип питания/кухни:", reply_markup=kb.cuisine_kb())
    await state.set_state(RecipeForm.cuisine)
    await callback.answer()


@dp.callback_query(F.data == "favorites:search")
async def favorites_search_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Напиши слово из названия или продукта рецепта для поиска:")
    await state.set_state(FavoritesForm.search)
    await callback.answer()


@dp.message(FavoritesForm.search)
async def favorites_search_run(message: Message, state: FSMContext):
    results = search_favorites(message.chat.id, message.text.strip())
    if results:
        await message.answer(
            f"🔎 Найдено рецептов: {len(results)}", reply_markup=kb.favorites_list_kb(results)
        )
    else:
        await message.answer(
            "Ничего не нашлось. Попробуй другое слово.",
            reply_markup=kb.favorites_list_kb(get_favorites(message.chat.id)),
        )


@dp.callback_query(F.data.startswith("fav:view:"))
async def favorite_view(callback: CallbackQuery):
    fav_id = callback.data.split(":", 2)[2]
    fav = get_favorite(callback.message.chat.id, fav_id)
    if not fav:
        await callback.answer("Рецепт не найден (возможно, уже удалён).", show_alert=True)
        return
    await callback.message.answer(fav["text"], reply_markup=kb.favorite_view_kb(fav_id))
    await callback.answer()


@dp.callback_query(F.data.startswith("fav:delete:"))
async def favorite_delete(callback: CallbackQuery):
    fav_id = callback.data.split(":", 2)[2]
    remove_favorite(callback.message.chat.id, fav_id)
    await callback.answer("Удалено из избранного")
    favs = get_favorites(callback.message.chat.id)
    if favs:
        await callback.message.answer("⭐ Твоё избранное:", reply_markup=kb.favorites_list_kb(favs))
    else:
        await callback.message.answer("Избранное теперь пусто.", reply_markup=kb.cuisine_kb())


@dp.callback_query(F.data == "fav:save")
async def favorite_save(callback: CallbackQuery):
    last = get_last_recipe(callback.message.chat.id)
    if not last:
        await callback.answer("Не нашёл рецепт для сохранения, попробуй ещё раз получить его.", show_alert=True)
        return
    add_favorite(callback.message.chat.id, last["title"], last["text"], last["cuisine"])
    await callback.answer("Добавлено в избранное ⭐")


# ---------- Любой другой текст вне анкеты ----------
# Должен быть зарегистрирован ПОСЛЕДНИМ. Если человек пишет что-то вне анкеты, возвращаем ему
# нижнее меню (оно могло пропасть) и главное меню — вместо тишины.

@dp.message(StateFilter(None), F.text)
async def fallback_text(message: Message, state: FSMContext):
    await message.answer("Не совсем понял 🙂 Вот меню:", reply_markup=kb.main_reply_kb())
    await show_main_menu(message, state)


# ---------- Запуск ----------

async def main():
    if not config.BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в .env")

    if config.REMINDER_ENABLED:
        setup_scheduler(bot, dp)

    await bot.set_my_commands([
        BotCommand(command="menu", description="Показать меню"),
        BotCommand(command="ready", description="Готовые меню"),
        BotCommand(command="app", description="Веб-версия для телефона"),
        BotCommand(command="help", description="Справка"),
    ])

    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
