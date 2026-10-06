"""
Ежедневное напоминание — сразу предлагает новый рецепт по сохранённым параметрам.
Кнопки: «Приготовить» (открывает «Найти продукты» / «В избранное») и «Найти другой рецепт» (анкета).
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from ai import generate_recipe
from last_recipe import set_last_recipe
from search import search_recipes, format_results_for_prompt
from shopping import extract_shopping_terms, extract_dish_title
from storage import get_all_users, save_last_request

logger = logging.getLogger(__name__)


def reminder_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🍳 Приготовить", callback_data="reminder:ok")
    b.button(text="🔍 Найти другой рецепт", callback_data="reminder:next")
    b.adjust(1)
    return b.as_markup()


REMINDER_INTRO = "👋 Идея, что приготовить завтра — по твоим прошлым параметрам:\n\n"
_PARALLEL = 3            # одновременно готовим рецепты для 3 пользователей (не перегружаем ИИ и поиск)
_PER_USER_TIMEOUT = 150  # секунд на одного пользователя: поиск + ИИ


async def _remind_one(bot: Bot, chat_id: int, data: dict) -> None:
    # Прошлые параметры, но без названия прошлого блюда — иначе ИИ снова предложит то же самое
    search_data = dict(data)
    search_data["preferred"] = ""
    search_data.pop("dish_title", None)

    search_query, results = await search_recipes(search_data)
    recipe_text = await generate_recipe(search_data, search_query, format_results_for_prompt(results))
    recipe_text, _terms = extract_shopping_terms(recipe_text)

    if not recipe_text.strip() or recipe_text.startswith("😔"):
        # ИИ недоступен — короткое напоминание без рецепта, чем ничего или текст ошибки
        await bot.send_message(
            chat_id, "👋 Как насчёт завтра приготовить что-нибудь новое?",
            reply_markup=InlineKeyboardBuilder().button(text="🍳 Подобрать рецепт", callback_data="menu:open").as_markup(),
        )
        return

    dish_title = extract_dish_title(recipe_text)
    save_last_request(chat_id, search_data, dish_title)
    set_last_recipe(chat_id, dish_title, recipe_text, search_data.get("cuisine"))
    await bot.send_message(chat_id, REMINDER_INTRO + recipe_text, reply_markup=reminder_kb())


async def send_daily_reminders(bot: Bot, dp: Dispatcher):
    users = get_all_users()
    if not users:
        logger.info("Напоминания: нет ни одного сохранённого пользователя")
        return
    sem = asyncio.Semaphore(_PARALLEL)

    async def guarded(chat_id: int, data: dict):
        async with sem:
            try:
                await asyncio.wait_for(_remind_one(bot, chat_id, data), timeout=_PER_USER_TIMEOUT)
            except TelegramForbiddenError:
                logger.info("Напоминание %s: пользователь заблокировал бота", chat_id)
            except Exception as e:  # noqa: BLE001 — один пользователь не должен ломать рассылку остальным
                logger.warning("Не удалось отправить напоминание %s: %r", chat_id, e)

    await asyncio.gather(*(guarded(int(cid), data) for cid, data in users.items()))
    logger.info("Напоминания разосланы: %d пользователей", len(users))


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
