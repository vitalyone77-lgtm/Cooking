"""
Ежедневное напоминание "как насчёт приготовить завтра?" в заданное время (по умолчанию 14:00).
Использует сохранённые параметры прошлого запроса (тип питания, исключения, время, порции,
техника), но не название конкретного блюда — так каждый раз предлагается новый вариант,
а не одно и то же блюдо.
"""
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from cuisines import CUISINE_LABELS
from states import RecipeForm
from storage import get_all_users

logger = logging.getLogger(__name__)


def reminder_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🍳 Приготовить", callback_data="confirm:go")
    b.button(text="🔍 Найти другой рецепт", callback_data="confirm:go")
    b.adjust(1)
    return b.as_markup()


async def send_daily_reminders(bot: Bot, dp: Dispatcher):
    users = get_all_users()
    if not users:
        logger.info("Напоминания: нет ни одного сохранённого пользователя")
        return

    for chat_id_str, data in users.items():
        chat_id = int(chat_id_str)
        cuisine_label = CUISINE_LABELS.get(data.get("cuisine"), "")

        text = (
            f"👋 Как насчёт завтра приготовить что-нибудь новое?\n"
            f"Подберу вариант по твоим обычным параметрам (тип питания: {cuisine_label}) — "
            f"но не то же самое, что в прошлый раз."
        )

        try:
            # Загружаем сохранённые параметры поиска (кухня/исключения/время/порции/техника)
            # прямо в FSM, чтобы кнопки сразу запускали поиск+ИИ без анкеты. Конкретное
            # название прошлого блюда сознательно не передаём — иначе ИИ снова предложит
            # то же самое (например, каждый день "спагетти").
            search_data = dict(data)
            search_data["preferred"] = ""
            search_data.pop("dish_title", None)

            key = StorageKey(bot_id=bot.id, chat_id=chat_id, user_id=chat_id)
            fsm = FSMContext(storage=dp.storage, key=key)
            await fsm.set_data(search_data)
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
