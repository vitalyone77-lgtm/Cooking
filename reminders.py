"""
Ежедневное напоминание — сразу предлагает новый рецепт по сохранённым параметрам.
Кнопки: «Приготовить» (сохранить рецепт) и «Найти другой рецепт» (переген).
"""
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import keyboards as kb
from cuisines import CUISINE_LABELS
from search import search_recipes, format_results_for_prompt
from ai import generate_recipe
from shopping import extract_shopping_terms, extract_dish_title
from states import RecipeForm
from storage import get_all_users, set_last_recipe, save_last_request

logger = logging.getLogger(__name__)


def reminder_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🍳 Приготовить", callback_data="reminder:ok")
    b.button(text="🔍 Найти другой рецепт", callback_data="reminder:next")
    b.adjust(1)
    return b.as_markup()


async def send_daily_reminders(bot: Bot, dp: Dispatcher):
    users = get_all_users()
    if not users:
        logger.info("Напоминания: нет ни одного сохранённого пользователя")
        return

    for chat_id_str, data in users.items():
        chat_id = int(chat_id_str)
        
        # Загружаем сохранённые параметры (без конкретного блюда, чтобы генерировать новый)
        search_data = dict(data)
        search_data["preferred"] = ""
        search_data.pop("dish_title", None)

        try:
            # Генерируем рецепт прямо в напоминании
            search_query, results = await search_recipes(search_data)
            results_text = format_results_for_prompt(results)
            recipe_text = await generate_recipe(search_data, search_query, results_text)
            recipe_text, shopping_terms = extract_shopping_terms(recipe_text)

            if not recipe_text.strip():
                # Если не получилось, отправляем старое сообщение
                await bot.send_message(
                    chat_id,
                    "👋 Как насчёт завтра приготовить что-нибудь новое? Напиши /menu и выбери параметры.",
                    reply_markup=InlineKeyboardBuilder().button(text="🍳 Выбрать рецепт", callback_data="menu:open").as_markup()
                )
                continue

            dish_title = extract_dish_title(recipe_text)
            save_last_request(chat_id, search_data, dish_title)
            set_last_recipe(chat_id, dish_title, recipe_text, search_data.get("cuisine"))

            # Отправляем готовый рецепт с кнопками напоминания
            await bot.send_message(chat_id, recipe_text, reply_markup=reminder_kb())

        except Exception as e:
            logger.warning(f"Не удалось отправить напоминание {chat_id}: {e}")


def setup_scheduler(bot: Bot, dp: Dispatcher) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=config.REMINDER_TIMEZONE)
    scheduler.add_job(
        send_daily_reminders,
        trigger=CronTrigger(hour=config.REMINDER_HOUR, minute=config.REMINDER_MINUTE),
        args=[bot, dp],
        id="daily_reminder",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        f"Напоминания включены: каждый день в {config.REMINDER_HOUR:02d}:{config.REMINDER_MINUTE:02d} "
        f"({config.REMINDER_TIMEZONE})"
    )
    return scheduler
