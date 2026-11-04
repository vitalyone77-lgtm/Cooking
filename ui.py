"""
Экраны бота без спама: переходы по меню правят то же сообщение (текст в текст, картинка в картинку),
а при смене вида (картинка <-> текст) старое сообщение удаляется и приходит новое. Рецепты, списки покупок и PDF
сюда не относятся: это содержимое, его не правим и не удаляем.

Режимы (mode):
  edit    — править нажатое сообщение (экраны профиля и дневника: это всегда экраны)
  auto    — править нажатое, только если это последний экран бота; иначе (кнопка под рецептом) прислать новый
  tracked — править последний экран (человек отвечает на вопрос текстом)
  fresh   — удалить прошлый экран и прислать новый внизу (команда или кнопка нижнего меню)
Последний экран каждого чата помним в памяти; после перезапуска бота первое нажатие пришлёт новое сообщение.
"""
import logging

from aiogram.types import BufferedInputFile, CallbackQuery, InputMediaPhoto, Message

logger = logging.getLogger(__name__)
_last: dict[int, tuple[int, bool]] = {}      # чат -> (id сообщения-экрана, это картинка?)
CAPTION_MAX = 1024


async def _delete(bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:  # noqa: BLE001 — старше 48 часов или уже удалено
        pass


async def drop(message: Message) -> None:
    """Убрать сообщение человека (его ответ на вопрос), чтобы чат не пух."""
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass


async def show(event, text: str, kb=None, photo: bytes | None = None, *, mode: str = "edit", parse_mode=None) -> int | None:
    """Показать экран. event — CallbackQuery или Message. Возвращает id сообщения-экрана."""
    msg = event.message if isinstance(event, CallbackQuery) else event
    bot, chat_id = msg.bot, msg.chat.id
    tracked = _last.get(chat_id)
    target: tuple[int, bool] | None = None
    if mode == "edit" and isinstance(event, CallbackQuery):
        target = (msg.message_id, bool(msg.photo))
    elif mode == "auto" and isinstance(event, CallbackQuery) and tracked and tracked[0] == msg.message_id:
        target = tracked
    elif mode == "tracked":
        target = tracked
    caption = text[:CAPTION_MAX]
    if target:
        mid, is_photo = target
        try:
            if photo is not None and is_photo:
                await bot.edit_message_media(InputMediaPhoto(media=BufferedInputFile(photo, "screen.png"), caption=caption, parse_mode=parse_mode),
                                             chat_id=chat_id, message_id=mid, reply_markup=kb)
                _last[chat_id] = (mid, True)
                return mid
            if photo is None and not is_photo:
                await bot.edit_message_text(text, chat_id=chat_id, message_id=mid, reply_markup=kb, parse_mode=parse_mode)
                _last[chat_id] = (mid, False)
                return mid
        except Exception as e:  # noqa: BLE001
            if "not modified" in str(e).lower():
                _last[chat_id] = (mid, is_photo)
                return mid
            logger.info("Экран не удалось поправить на месте (%s) — пришлю новый", e)
        await _delete(bot, chat_id, mid)                  # вид сменился (картинка <-> текст) или правка не вышла
    elif tracked and (not isinstance(event, CallbackQuery) or tracked[0] != msg.message_id):
        await _delete(bot, chat_id, tracked[0])           # открываем новый экран: прошлый убираем
    if tracked and target and tracked[0] != target[0]:
        await _delete(bot, chat_id, tracked[0])
    if photo is not None:
        sent = await bot.send_photo(chat_id, BufferedInputFile(photo, "screen.png"), caption=caption, reply_markup=kb, parse_mode=parse_mode)
    else:
        sent = await bot.send_message(chat_id, text, reply_markup=kb, parse_mode=parse_mode)
    _last[chat_id] = (sent.message_id, photo is not None)
    return sent.message_id


def forget(chat_id: int) -> None:
    _last.pop(chat_id, None)
