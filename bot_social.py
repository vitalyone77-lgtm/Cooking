"""
/postvk — черновик «рецепта дня» для владельца: смотрим текст и картинку и жмём «Опубликовать» (сообщество ВК и/или канал).
Если картинки бесплатные (SOCIAL_IMAGES), карточка делается сразу вместе с черновиком; если платные (Leonardo), она рисуется
по кнопке «С картинкой». Команда только для ADMIN_TG_IDS.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import accounts
import config
import feature_posts
import images
import social

logger = logging.getLogger(__name__)
router = Router(name="social")
_images: dict[int, bytes] = {}      # картинка к черновику владельца (только в памяти, пока не опубликована)


def _is_admin(chat_id: int) -> bool:
    return chat_id in config.ADMIN_TG_IDS


def _kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Опубликовать", callback_data="soc:go")
    if images.configured():
        b.button(text="🖼 С картинкой", callback_data="soc:img")
    b.button(text="🔄 Другой рецепт", callback_data="soc:next")
    b.button(text="Отмена", callback_data="soc:no")
    b.adjust(1, 2, 1)
    return b.as_markup()


def _image_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Опубликовать с картинкой", callback_data="soc:goimg")
    b.button(text="🔄 Другая картинка", callback_data="soc:img")
    b.button(text="Без картинки", callback_data="soc:go")
    b.button(text="Отмена", callback_data="soc:no")
    b.adjust(1, 2, 1)
    return b.as_markup()


def _where() -> str:
    parts = (["сообщество ВКонтакте"] if social.configured_vk() else []) + (["Telegram-канал"] if social.configured_tg() else [])
    return " и ".join(parts)


async def _send_image(bot, chat_id: int, draft: dict) -> None:
    wait = await bot.send_message(chat_id, "⏳ Делаю картинку…", parse_mode=None)
    img = await social.build_image(draft["title"], draft.get("body") or draft["text"])
    await wait.delete()
    if not img:
        await bot.send_message(chat_id, "😔 Картинка не получилась. Опубликовать без неё или попробовать ещё раз?",
                               parse_mode=None, reply_markup=_kb())
        return
    _images[chat_id] = img
    await bot.send_photo(chat_id, BufferedInputFile(img, "dish.png"), caption="Картинка к рецепту", reply_markup=_image_kb())


async def _draft(bot, chat_id: int, variant: int = 0) -> None:
    _images.pop(chat_id, None)
    try:
        d = await social.build_post_full(variant)
    except Exception as e:  # noqa: BLE001
        await bot.send_message(chat_id, f"😔 Не получилось составить рецепт: {e}", parse_mode=None)
        return
    draft = {"title": d["title"], "text": d["text"], "body": d["body"], "variant": variant, "section": d["section"]}
    accounts.sess_set(f"soc:draft:{chat_id}", draft)
    auto = config.SOCIAL_IMAGES and images.configured()
    await bot.send_message(chat_id, f"Черновик ({_where()}):\n\n{d['text'][:3500]}", parse_mode=None,
                           reply_markup=None if auto else _kb())
    if auto:
        await _send_image(bot, chat_id, draft)


# ---------- посты о функциях (/postfunc): только по нажатию владельца, по одному ----------

def _func_kb() -> InlineKeyboardMarkup:
    done = social.feature_done()
    b = InlineKeyboardBuilder()
    for key, title, _, _, _ in feature_posts.POSTS:
        mark = "✅ " if key in done else ("🔒 " if not social.feature_open(key) else "")
        b.button(text=mark + title, callback_data=f"sf:v:{key}")
    b.adjust(1)
    return b.as_markup()


@router.message(Command("postfunc"))
async def cmd_postfunc(message: Message):
    if not _is_admin(message.chat.id):
        return
    await message.answer("Посты о функциях. ✅ уже опубликован, 🔒 функция открыта не всем, такой пост лучше отложить. Выберите пост:", reply_markup=_func_kb(), parse_mode=None)


@router.callback_query(F.data.startswith("sf:v:"))
async def func_view(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    if not _is_admin(callback.message.chat.id) or key not in feature_posts.BY_KEY:
        await callback.answer()
        return
    await callback.answer()
    b = InlineKeyboardBuilder()
    b.button(text="✅ Опубликовать в ВК", callback_data=f"sf:go:{key}")
    b.button(text="⬅️ К списку", callback_data="sf:l")
    b.adjust(1)
    await callback.message.edit_text(social.feature_text(key)[:3900], reply_markup=b.as_markup(), parse_mode=None, disable_web_page_preview=True)


@router.callback_query(F.data == "sf:l")
async def func_list(callback: CallbackQuery):
    if not _is_admin(callback.message.chat.id):
        await callback.answer()
        return
    await callback.answer()
    await callback.message.edit_text("Посты о функциях. ✅ уже опубликован, 🔒 функция открыта не всем, такой пост лучше отложить. Выберите пост:", reply_markup=_func_kb(), parse_mode=None)


@router.callback_query(F.data.startswith("sf:go:"))
async def func_go(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    if not _is_admin(callback.message.chat.id) or key not in feature_posts.BY_KEY:
        await callback.answer()
        return
    if not social.configured_vk():
        await callback.answer("ВКонтакте не настроен", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_reply_markup()               # второе нажатие ничего не опубликует
    try:
        post = await social.publish_feature(key)
    except RuntimeError as e:
        await callback.message.answer(f"⚠️ {e}", parse_mode=None)
        return
    await callback.message.answer(f"✅ ВКонтакте: запись №{post} опубликована", reply_markup=_func_kb(), parse_mode=None)


@router.message(Command("postvk"))
async def cmd_postvk(message: Message):
    if not _is_admin(message.chat.id):
        return
    if not (social.configured_vk() or social.configured_tg()):
        await message.answer("ВКонтакте и канал не настроены. Добавьте VK_GROUP_ID и VK_GROUP_TOKEN (или TG_CHANNEL_ID) "
                             "в .env на сервере, подробности в KEYS.md.", parse_mode=None)
        return
    wait = await message.answer("⏳ Придумываю рецепт дня…", parse_mode=None)
    await _draft(message.bot, message.chat.id)
    await wait.delete()


@router.callback_query(F.data == "soc:next")
async def next_recipe(callback: CallbackQuery):
    if not _is_admin(callback.message.chat.id):
        await callback.answer()
        return
    draft = accounts.sess_get(f"soc:draft:{callback.message.chat.id}") or {}
    await callback.answer("Придумываю другой…")
    await callback.message.edit_reply_markup()
    await _draft(callback.bot, callback.message.chat.id, draft.get("variant", 0) + 1)


@router.callback_query(F.data == "soc:no")
async def cancel(callback: CallbackQuery):
    _images.pop(callback.message.chat.id, None)
    await callback.answer("Отменил")
    await callback.message.edit_reply_markup()


@router.callback_query(F.data == "soc:img")
async def draw(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    if not _is_admin(chat_id):
        await callback.answer()
        return
    draft = accounts.sess_get(f"soc:draft:{chat_id}")
    if not draft or not draft.get("text"):
        await callback.answer("Черновик устарел, вызовите /postvk ещё раз", show_alert=True)
        return
    await callback.answer()
    await _send_image(callback.bot, chat_id, draft)


async def _publish(callback: CallbackQuery, with_image: bool) -> None:
    chat_id = callback.message.chat.id
    if not _is_admin(chat_id):
        await callback.answer()
        return
    draft = accounts.sess_get(f"soc:draft:{chat_id}")
    img = _images.get(chat_id) if with_image else None
    if not draft or not draft.get("text") or (with_image and not img):
        await callback.answer("Черновик устарел, вызовите /postvk ещё раз", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_reply_markup()
    accounts.sess_set(f"soc:draft:{chat_id}", {})          # второе нажатие ничего не опубликует
    _images.pop(chat_id, None)
    res = await social.publish(callback.bot, draft["title"], draft["text"], img, draft.get("body"), draft.get("section"))
    lines = []
    if res["vk"]:
        lines.append(f"✅ ВКонтакте: запись №{res['vk']} опубликована")
    if res["tg"]:
        lines.append("✅ Telegram-канал: опубликовано")
    lines += [f"⚠️ {e}" for e in res["errors"]]
    await callback.message.answer("\n".join(lines) or "Некуда публиковать", parse_mode=None)


@router.callback_query(F.data == "soc:go")
async def go(callback: CallbackQuery):
    await _publish(callback, with_image=False)


@router.callback_query(F.data == "soc:goimg")
async def go_with_image(callback: CallbackQuery):
    await _publish(callback, with_image=True)
