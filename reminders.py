"""
Ежедневное напоминание "как насчёт приготовить завтра?" в заданное время (по умолчанию 14:00).
Предлагает ту же кухню/блюдо, что было в прошлый раз, с кнопкой "Найти рецепт",
которая запускает обычный поиск+ИИ с сохранёнными параметрами.
"""
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from states import RecipeForm
from storage import get_all_users

logger = logging.getLogger(__name__)


def reminder_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🔍 Найти рецепт", callback_data="confirm:go")
    b.button(text="🍳 Хочу что-то другое", callback_data="confirm:restart")
    b.adjust(1)
    return b.as_markup()


async def send_daily_reminders(bot: Bot, dp: Dispatcher):
    users = get_all_users()
    if not users:
        logger.info("Напоминания: нет ни одного сохранённого пользователя")
        return

    cuisine_labels = {"ayurveda": "аюрведическую", "classic": "классическую"}

    for chat_id_str, data in users.items():
        chat_id = int(chat_id_str)
        dish = data.get("dish_title") or ""
        cuisine_label = cuisine_labels.get(data.get("cuisine"), "")

        if dish:
            text = (
                f"👋 Как насчёт приготовить завтра «{dish}» — как в прошлый раз?\n"
                f"Или подберём что-то новое на {cuisine_label} кухню?"
            )
        else:
            text = f"👋 Как насчёт завтра приготовить что-нибудь на {cuisine_label} кухню?"

        try:
            # Загружаем сохранённые параметры прямо в FSM, чтобы кнопка "Найти рецепт"
            # сразу могла запустить поиск+ИИ без повторного прохождения анкеты.
            key = StorageKey(bot_id=bot.id, chat_id=chat_id, user_id=chat_id)
            fsm = FSMContext(storage=dp.storage, key=key)
            await fsm.set_data(data)
            await fsm.set_state(RecipeForm.confirm)

            await bot.send_message(chat_id, text, reply_markup=reminder_kb())
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
