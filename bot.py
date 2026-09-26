"""
Кухонный бот-помощник для мамы 🍲
Анкета (кухня → продукты → исключения → время → порции → техника) →
поиск рецептов в интернете → анализ ИИ → готовый рецепт с продуктами и инструкцией.

Запуск: python bot.py
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery

import config
import keyboards as kb
import day_menu
from cuisines import cuisine_label, MACRO_GOAL_KEY
from states import RecipeForm, FavoritesForm, DayMenuForm
from search import search_recipes, format_results_for_prompt
from ai import generate_recipe
from shopping import extract_shopping_terms, format_shopping_message, extract_dish_title
from storage import save_last_request
from reminders import setup_scheduler
from favorites import add_favorite, get_favorites, get_favorite, remove_favorite, search_favorites
from last_recipe import set_last_recipe, get_last_recipe

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


# ---------- Старт анкеты ----------

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    # Reply-клавиатура и инлайн-клавиатура — разные типы reply_markup, за один вызов
    # можно прикрепить только один, поэтому нижнее меню отправляем отдельным сообщением.
    await message.answer("Меню открыто внизу ⌨️ — доступно в любой момент.", reply_markup=kb.main_reply_kb())
    await message.answer(
        "Привет! 👋 Я помогу подобрать рецепт под твои предпочтения.\n\n"
        "Для начала выбери тип питания/кухни:",
        reply_markup=kb.cuisine_kb(),
    )
    await state.set_state(RecipeForm.cuisine)


@dp.callback_query(F.data == "confirm:restart")
async def restart(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "Хорошо, начнём заново! Выбери тип питания/кухни:",
        reply_markup=kb.cuisine_kb(),
    )
    await state.set_state(RecipeForm.cuisine)
    await callback.answer()


# ---------- Постоянное нижнее меню (reply-keyboard) ----------
# Зарегистрированы раньше остальных message-хэндлеров, чтобы иметь приоритет: даже если
# пользователь сейчас в середине анкеты (ждём текст для preferred/excluded/...), нажатие
# кнопки нижнего меню должно перехватывать управление, а не восприниматься как ответ на
# текущий шаг анкеты.

@dp.message(F.text == kb.BTN_COOK)
async def menu_btn_cook(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Выбери тип питания/кухни:", reply_markup=kb.cuisine_kb())
    await state.set_state(RecipeForm.cuisine)


@dp.message(F.text == kb.BTN_DAY_MENU)
async def menu_btn_day_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📅 Соберём меню на день! Выбери приёмы пищи (можно несколько), потом «Готово»:",
        reply_markup=kb.meals_kb(set()),
    )
    await state.set_state(DayMenuForm.meals)


@dp.message(F.text == kb.BTN_FAVORITES)
async def menu_btn_favorites(message: Message, state: FSMContext):
    await state.clear()
    favs = get_favorites(message.chat.id)
    if favs:
        await message.answer(f"⭐ Твоё избранное ({len(favs)}):", reply_markup=kb.favorites_list_kb(favs))
    else:
        await message.answer(
            "Пока в избранном пусто. Понравившийся рецепт можно сохранить кнопкой "
            "«⭐ В избранное» после его получения.",
            reply_markup=kb.cuisine_kb(),
        )
        await state.set_state(RecipeForm.cuisine)


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

        shopping_message = format_shopping_message(shopping_terms)
        full_display_text = recipe_text + ("\n\n" + shopping_message if shopping_message else "")
        set_last_recipe(callback.message.chat.id, dish_title, full_display_text, data.get("cuisine"))
    except Exception as e:
        logger.exception("Ошибка при подборе рецепта")
        await status_msg.edit_text(
            f"😔 Что-то пошло не так при подборе рецепта.\nТехническая причина: {e}\n\n"
            "Попробуй ещё раз.",
            reply_markup=kb.restart_kb(),
        )
        return

    await status_msg.delete()
    await callback.message.answer(recipe_text)

    if shopping_message:
        await callback.message.answer(
            shopping_message,
            reply_markup=kb.recipe_result_kb(),
            disable_web_page_preview=True,
        )
    else:
        await callback.message.answer("Готово! 🍽", reply_markup=kb.recipe_result_kb())


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
        await callback.message.answer("Теперь выбери тип питания/кухни:", reply_markup=kb.cuisine_kb())
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

    text, markup = day_menu.build_summary_view(callback.message.chat.id)
    await callback.message.answer(text, reply_markup=markup)

    if summary["shopping_message"]:
        await callback.message.answer(summary["shopping_message"], disable_web_page_preview=True)


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


# ---------- Запуск ----------

async def main():
    if not config.BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в .env")

    if config.REMINDER_ENABLED:
        setup_scheduler(bot, dp)

    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
