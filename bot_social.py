"""
/postvk — черновик «рецепта дня» для владельца: смотрим текст и жмём «Опубликовать» (сообщество ВК и/или канал).
Команда только для ADMIN_TG_IDS.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import accounts
import config
import social

logger = logging.getLogger(__name__)
router = Router(name="social")


def _is_admin(chat_id: int) -> bool:
    return chat_id in config.ADMIN_TG_IDS


def _kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Опубликовать", callback_data="soc:go")
    b.button(text="🔄 Другой рецепт", callback_data="soc:next")
    b.button(text="Отмена", callback_data="soc:no")
    b.adjust(1, 2)
    return b.as_markup()


def _where() -> str:
    parts = (["сообщество ВКонтакте"] if social.configured_vk() else []) + (["Telegram-канал"] if social.configured_tg() else [])
    return " и ".join(parts)


async def _draft(chat_id: int, send, variant: int = 0) -> None:
    try:
        title, text = await social.build_post(variant)
    except Exception as e:  # noqa: BLE001
        await send(f"😔 Не получилось составить рецепт: {e}")
        return
    accounts.sess_set(f"soc:draft:{chat_id}", {"title": title, "text": text, "variant": variant})
    await send(f"Черновик ({_where()}):\n\n{text[:3500]}", reply_markup=_kb())


@router.message(Command("postvk"))
async def cmd_postvk(message: Message):
    if not _is_admin(message.chat.id):
        return
    if not (social.configured_vk() or social.configured_tg()):
        await message.answer("ВКонтакте и канал не настроены. Добавьте VK_GROUP_ID и VK_GROUP_TOKEN (или TG_CHANNEL_ID) "
                             "в .env на сервере, подробности в KEYS.md.", parse_mode=None)
        return
    wait = await message.answer("⏳ Придумываю рецепт дня…", parse_mode=None)
    await _draft(message.chat.id, lambda t, **k: message.answer(t, parse_mode=None, **k))
    await wait.delete()


@router.callback_query(F.data == "soc:next")
async def next_recipe(callback: CallbackQuery):
    if not _is_admin(callback.message.chat.id):
        await callback.answer()
        return
    draft = accounts.sess_get(f"soc:draft:{callback.message.chat.id}") or {}
    await callback.answer("Придумываю другой…")
    await callback.message.edit_reply_markup()
    await _draft(callback.message.chat.id, lambda t, **k: callback.message.answer(t, parse_mode=None, **k), draft.get("variant", 0) + 1)


@router.callback_query(F.data == "soc:no")
async def cancel(callback: CallbackQuery):
    await callback.answer("Отменил")
    await callback.message.edit_reply_markup()


@router.callback_query(F.data == "soc:go")
async def go(callback: CallbackQuery):
    if not _is_admin(callback.message.chat.id):
        await callback.answer()
        return
    draft = accounts.sess_get(f"soc:draft:{callback.message.chat.id}")
    if not draft:
        await callback.answer("Черновик устарел, вызовите /postvk ещё раз", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_reply_markup()
    accounts.sess_set(f"soc:draft:{callback.message.chat.id}", {})          # второе нажатие ничего не опубликует
    res = await social.publish(callback.bot, draft["title"], draft["text"])
    lines = []
    if res["vk"]:
        lines.append(f"✅ ВКонтакте: запись №{res['vk']} опубликована")
    if res["tg"]:
        lines.append("✅ Telegram-канал: опубликовано")
    lines += [f"⚠️ {e}" for e in res["errors"]]
    await callback.message.answer("\n".join(lines) or "Некуда публиковать", parse_mode=None)
