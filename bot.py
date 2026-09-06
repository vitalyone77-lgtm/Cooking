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
from states import RecipeForm, FavoritesForm
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
    await message.answer(
        "Привет! 👋 Я помогу подобрать рецепт под твои предпочтения.\n\n"
        "Для начала выбери тип кухни:",
        reply_markup=kb.cuisine_kb(),
    )
    await state.set_state(RecipeForm.cuisine)


@dp.callback_query(F.data == "confirm:restart")
async def restart(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "Хорошо, начнём заново! Выбери тип кухни:",
        reply_markup=kb.cuisine_kb(),
    )
    await state.set_state(RecipeForm.cuisine)
    await callback.answer()


# ---------- Шаг 1: кухня ----------

@dp.callback_query(RecipeForm.cuisine, F.data.startswith("cuisine:"))
async def step_cuisine(callback: CallbackQuery, state: FSMContext):
    cuisine = callback.data.split(":", 1)[1]
    await state.update_data(cuisine=cuisine)
    await callback.message.edit_reply_markup()
    await callback.message.answer(
        "Отлично! Теперь напиши, какие продукты ты хочешь использовать "
        "(например: курица, рис, брокколи) — или сразу название блюда, "
        "которое хочешь приготовить (например: «хочу плов» или «хочу борщ»).\n"
        "Если без разницы — нажми «Пропустить».",
        reply_markup=kb.skip_kb("preferred"),
    )
    await state.set_state(RecipeForm.preferred)
    await callback.answer()


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
    cuisine_label = "Аюрведическая" if data.get("cuisine") == "ayurveda" else "Классическая"
    text = (
        "📋 Проверим запрос:\n\n"
        f"Кухня: {cuisine_label}\n"
        f"Предпочитаемые продукты: {data.get('preferred') or '—'}\n"
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

    search_query, results = await search_recipes(data)
    await status_msg.edit_text(f"🔎 Искал по запросу: «{search_query}»\n🤖 Анализирую и подбираю рецепт...")

    results_text = format_results_for_prompt(results)
    recipe_text = await generate_recipe(data, search_query, results_text)
    recipe_text, shopping_terms = extract_shopping_terms(recipe_text)
    dish_title = extract_dish_title(recipe_text)

    save_last_request(callback.message.chat.id, data, dish_title)

    shopping_message = format_shopping_message(shopping_terms)
    full_display_text = recipe_text + ("\n\n" + shopping_message if shopping_message else "")
    set_last_recipe(callback.message.chat.id, dish_title, full_display_text, data.get("cuisine"))

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
    await callback.message.answer("Выбери тип кухни:", reply_markup=kb.cuisine_kb())
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
