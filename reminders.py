"""
Ежедневное напоминание — сразу предлагает новый рецепт по сохранённым параметрам.
В сообщении — только название и время; кнопки: «Готовить» (полный рецепт), «Найти другое» (анкета), «Выключить напоминания».
"""
import asyncio
import logging
import random

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from ai import generate_recipe
from search import search_recipes, format_results_for_prompt
from shopping import extract_shopping_terms, extract_dish_title
from storage import get_all_users, save_last_request, save_reminder_recipe, push_reminder_history

logger = logging.getLogger(__name__)


def reminder_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🍳 Готовить", callback_data="reminder:cook")
    b.button(text="🔍 Найти другое", callback_data="reminder:next")
    b.button(text="🔕 Выключить напоминания", callback_data="reminder:off")
    b.adjust(2, 1)
    return b.as_markup()


REMINDER_INTRO = "👋 Идея, давай приготовим завтра —"
# Тип блюда на сегодня — по кругу случайно, чтобы напоминания не предлагали каждый день похожее
VARIETY_HINTS = ["суп", "рагу или тушёное блюдо", "запеканка", "сытный салат", "каша или блюдо из крупы",
                 "паста или лапша", "котлеты или тефтели", "блюдо из яиц", "блюдо из бобовых",
                 "блюдо из овощей", "блюдо из рыбы", "блюдо из птицы", "блюдо из мяса", "плов или ризотто"]
_PARALLEL = 3            # одновременно готовим рецепты для 3 пользователей (не перегружаем ИИ и поиск)
_PER_USER_TIMEOUT = 150  # секунд на одного пользователя: поиск + ИИ


def recipe_teaser(recipe_text: str) -> str:
    """Короткое превью рецепта: строка с названием (🍽) и строка «⏱ Время | 👥 Порций | 🔥 Способ»."""
    lines = [ln.strip() for ln in recipe_text.splitlines() if ln.strip()]
    title = next((ln for ln in lines if ln.startswith("🍽")), lines[0] if lines else "")
    meta = next((ln for ln in lines if ln.startswith("⏱")), "")
    return "\n\n".join(x for x in (REMINDER_INTRO, title, meta) if x)


async def _remind_one(bot: Bot, chat_id: int, data: dict) -> None:
    # Прошлые параметры, но без названия прошлого блюда — иначе ИИ снова предложит то же самое
    search_data = {k: v for k, v in data.items()
                   if k not in ("reminders_off", "reminder_recipe", "reminder_history", "dish_title")}
    search_data["preferred"] = ""
    history = data.get("reminder_history") or []
    recent_hints = {h.get("hint") for h in history[-5:]}
    hint = random.choice([h for h in VARIETY_HINTS if h not in recent_hints] or VARIETY_HINTS)
    gen_data = dict(search_data, variety_hint=hint,
                    avoid_titles=[h["title"] for h in history if h.get("title")] + ([data["dish_title"]] if data.get("dish_title") else []))

    search_query, results = await search_recipes(gen_data)
    recipe_text = await generate_recipe(gen_data, search_query, format_results_for_prompt(results))
    recipe_text, terms = extract_shopping_terms(recipe_text)

    if not recipe_text.strip() or recipe_text.startswith("😔"):
        # ИИ недоступен — короткое напоминание без рецепта, чем ничего или текст ошибки
        await bot.send_message(
            chat_id, "👋 Как насчёт завтра приготовить что-нибудь новое?",
            reply_markup=InlineKeyboardBuilder().button(text="🍳 Подобрать рецепт", callback_data="menu:open").as_markup(),
        )
        return

    title = extract_dish_title(recipe_text)
    save_last_request(chat_id, search_data, title)
    push_reminder_history(chat_id, title, hint)
    # Полный рецепт храним в файле (переживёт перезапуск бота) и показываем по кнопке «Готовить»
    save_reminder_recipe(chat_id, recipe_text, terms)
    await bot.send_message(chat_id, recipe_teaser(recipe_text), reply_markup=reminder_kb())


async def send_daily_reminders(bot: Bot, dp: Dispatcher):
    users = {cid: d for cid, d in get_all_users().items() if d.get("cuisine") and not d.get("reminders_off")}
    if not users:
        logger.info("Напоминания: некому отправлять")
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
