"""
Сообщения разработчику («идея», «ошибка», «вопрос»): текст хранится в accounts.feedback, владельцу (ADMIN_TG_IDS)
приходит уведомление в Telegram. Сайт отправляет через HTTP-запрос к Telegram, бот — своим объектом Bot.
"""
import logging

import httpx

import accounts
import config

logger = logging.getLogger(__name__)


def admin_text(fid: int, kind: str, src: str, uid, text: str, contact: str = "", username: str = "") -> str:
    who = f"от {uid}" + (f" @{username}" if username else "") + f" ({'бот' if src == 'bot' else 'сайт'})"
    out = f"📨 Сообщение разработчику №{fid}: {accounts.FEEDBACK_KINDS.get(kind, kind).lower()}\n{who}"
    if contact:
        out += f"\nКонтакт: {contact}"
    return out + "\n\n" + text[:3000]


def notify_sync(text: str) -> None:
    """Из сайта: отправить владельцу в Telegram. Сбой не должен мешать человеку."""
    if not config.BOT_TOKEN:
        return
    for admin in config.ADMIN_TG_IDS:
        try:
            httpx.post(f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage",
                       json={"chat_id": admin, "text": text[:3900]}, timeout=5)
        except Exception as e:  # noqa: BLE001
            logger.warning("Уведомление о сообщении не ушло: %s", e)


async def notify_bot(bot, text: str) -> None:
    for admin in config.ADMIN_TG_IDS:
        try:
            await bot.send_message(admin, text[:3900], parse_mode=None)
        except Exception as e:  # noqa: BLE001
            logger.warning("Уведомление о сообщении не ушло: %s", e)
