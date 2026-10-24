"""
Страховка всех запросов бота к Telegram (подключается один раз к bot.session):
- ИИ иногда пишет «_» или «*» без пары — Markdown ломается, Telegram отвечает «can't parse entities»
  и пользователь не получает ничего. Тогда повторяем то же сообщение без разметки.
- Текст длиннее 4096 символов Telegram не принимает — режем на части по абзацам,
  кнопки — на последней части.
- «message is not modified» при повторном нажатии той же кнопки — не ошибка, молча пропускаем.
"""
import logging

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageReplyMarkup, EditMessageText, SendMessage

logger = logging.getLogger(__name__)

TG_LIMIT = 4096
_PART = 3900


def split_text(text: str, limit: int = _PART) -> list[str]:
    """Режет текст на части не длиннее limit, по возможности по переносам строк."""
    parts, buf = [], ""
    for para in text.split("\n"):
        while len(para) > limit:  # одна строка длиннее лимита — режем жёстко
            if buf:
                parts.append(buf)
                buf = ""
            parts.append(para[:limit])
            para = para[limit:]
        if buf and len(buf) + 1 + len(para) > limit:
            parts.append(buf)
            buf = para
        else:
            buf = f"{buf}\n{para}" if buf else para
    if buf:
        parts.append(buf)
    return parts or [""]


class SafeSendMiddleware(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        if isinstance(method, SendMessage) and len(method.text or "") > TG_LIMIT:
            parts = split_text(method.text)
            result = None
            for i, part in enumerate(parts):
                last = i == len(parts) - 1
                piece = method.model_copy(update={"text": part, "reply_markup": method.reply_markup if last else None})
                result = await self(make_request, bot, piece)
            return result
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "not modified" in msg and isinstance(method, (EditMessageText, EditMessageReplyMarkup)):
                return True
            if "parse entities" in msg and isinstance(method, (SendMessage, EditMessageText)) \
                    and method.parse_mode is not None:
                logger.info("Разметка сломана (%s) — отправляю без неё", e)
                return await make_request(bot, method.model_copy(update={"parse_mode": None, "entities": None}))
            raise


# ---------- защита от двойного нажатия ----------

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

# Кнопки, за которыми долгая и платная работа (поиск, ИИ, ВкусВилл, PDF). Пока одна такая работа
# в чате идёт, повторное нажатие (двойной тап, нетерпеливое «ещё раз») не запускает вторую копию.
HEAVY_PREFIXES = ("confirm:go", "miss:go:", "shop:cart:", "weekmenu:cook_next", "weekmenu:slot:", "pdf:")


class OneHeavyAtATime(BaseMiddleware):
    def __init__(self):
        self._busy: set[int] = set()

    async def __call__(self, handler, event: CallbackQuery, data):
        cb = event.data or ""
        if not cb.startswith(HEAVY_PREFIXES) or not event.message:
            return await handler(event, data)
        chat_id = event.message.chat.id
        if chat_id in self._busy:
            try:
                await event.answer("⏳ Уже готовлю — подожди немного, пришлю результат.")
            except Exception:  # noqa: BLE001 — старый callback, Telegram его уже не принимает
                pass
            return None
        self._busy.add(chat_id)
        try:
            return await handler(event, data)
        finally:
            self._busy.discard(chat_id)
