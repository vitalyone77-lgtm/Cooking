"""
Фото и голос в боте (новые функции — включаются в админке: тестировщикам, подписке или всем).

- 🎙 Голосовое = текст: распознаём и передаём дальше как обычное сообщение, поэтому голосом можно отвечать
  на любой шаг анкеты (продукты, исключения, цель КБЖУ, полка). Вне анкеты — сразу «хочу …» → подбор рецепта.
- 📷 Фото холодильника → продукты → отметить нужные → рецепт из них (обычный подбор, с профилем и лимитами).
- 📷 Фото полки (в «Полке» → «Добавить») → продукты → отметить → на полку.
"""
import asyncio
import logging
import time

from aiogram import F, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import accounts
import config
import plans
import usage
import vision
from states import ProfileForm, RecipeForm

logger = logging.getLogger(__name__)
router = Router(name="vision")

# bot.py подставляет: start_recipe(message, state, preferred) — анкета рецепта с готовыми продуктами;
# set_preferred(message, state, preferred) — продукты на шаге «что использовать» и дальше по анкете;
# shelf_view(uid) -> (text, markup)
hooks: dict = {}

PICK_TTL = 6 * 3600
_picks: dict[tuple[int, int], dict] = {}     # (chat_id, message_id) -> {mode, items, on, ts}
ALBUM_WAIT = 1.5                             # альбом приходит пачкой отдельных сообщений — ждём остальные фото
_albums: dict[str, list[Message]] = {}
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
IS_IMAGE = F.photo | F.document.mime_type.in_(IMAGE_TYPES)


class VisionForm(StatesGroup):
    extra = State()          # дописать продукты к распознанным


# ---------- голос ----------

async def voice_middleware(handler, event: Message, data: dict):
    """Голосовое → текст до фильтров: дальше его обработает тот же хэндлер, что и напечатанный текст."""
    if not event.voice or event.text:
        return await handler(event, data)
    uid = event.chat.id
    if not plans.feature_on(uid, "voice"):
        return await handler(event, data)
    if event.voice.duration and event.voice.duration > 90:
        await event.answer("🎙 Голосовое длинновато — уложись в минуту 🙂")
        return None
    if usage.events_today("voice", uid) >= config.VOICE_DAILY:
        await event.answer("🎙 На сегодня голосовых хватит 🙂 Можно написать текстом.")
        return None
    try:
        audio = await event.bot.download(event.voice.file_id)
        text = await vision.transcribe(audio.read())
    except vision.VisionError:
        await event.answer("🎙 Не получилось распознать голос — попробуй ещё раз или напиши текстом.")
        return None
    except Exception:  # noqa: BLE001 — скачивание из Telegram
        logger.exception("голосовое")
        await event.answer("🎙 Не получилось распознать голос — попробуй ещё раз или напиши текстом.")
        return None
    usage.event("voice", uid)
    if not text:
        await event.answer("🎙 Ничего не расслышал — попробуй ещё раз.")
        return None
    await event.answer(f"🎙 Распознал: «{text[:300]}»", parse_mode=None)
    state: FSMContext | None = data.get("state")
    start = hooks.get("start_recipe")
    if state is not None and start and await state.get_state() is None:
        await start(event, state, text[:300])      # вне анкеты: «хочу плов» — сразу к подбору
        return None
    result = await handler(event.model_copy(update={"text": text[:1000]}), data)
    if result is UNHANDLED and state is not None and start:
        await start(event, state, text[:300])      # шаг, где текст не ждут (например, выбор кухни кнопкой)
        return None
    return result


# ---------- фото ----------

def _pick_kb(key: tuple[int, int]) -> InlineKeyboardMarkup:
    p = _picks[key]
    rows, row = [], []
    for i, name in enumerate(p["items"]):
        row.append(InlineKeyboardButton(text=("✅ " if i in p["on"] else "▫️ ") + name[:28], callback_data=f"vp:t:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    n = len(p["on"])
    go = f"🍳 Подобрать рецепт ({n})" if p["mode"] != "shelf" else f"🏠 Добавить на полку ({n})"
    rows.append([InlineKeyboardButton(text=go, callback_data="vp:go")])
    rows.append([InlineKeyboardButton(text="✏️ Дописать", callback_data="vp:add"),
                 InlineKeyboardButton(text="❌ Отмена", callback_data="vp:no")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pick_text(p: dict) -> str:
    head = {"shelf": "📷 Вот что я вижу на фото. Сними галочки с лишнего — остальное добавлю на полку.",
            "form": "📷 Вот что я вижу на фото. Сними галочки с лишнего — из остального подберу рецепт.",
            "new": "📷 Вот что я вижу на фото. Сними галочки с лишнего — из остального подберу рецепт."}[p["mode"]]
    return head + "\nЧего-то не хватает — «✏️ Дописать»."


def _prune() -> None:
    old = time.time() - PICK_TTL
    for k in [k for k, v in _picks.items() if v["ts"] < old]:
        _picks.pop(k, None)


async def _photo_bytes(message: Message) -> tuple[bytes, str] | None:
    if message.photo:
        f = await message.bot.download(message.photo[-1].file_id)
        return f.read(), "image/jpeg"
    doc = message.document
    if doc and (doc.mime_type or "") in IMAGE_TYPES and (doc.file_size or 0) < 15_000_000:
        f = await message.bot.download(doc.file_id)
        return f.read(), doc.mime_type
    return None


async def _recognize(message: Message, state: FSMContext, mode: str) -> None:
    """Альбом (холодильник + полка одним отправлением) — одна попытка и один общий список."""
    group = message.media_group_id
    if group:
        if group in _albums:
            _albums[group].append(message)
            return
        _albums[group] = [message]
        await asyncio.sleep(ALBUM_WAIT)
        messages = _albums.pop(group, [message])
    else:
        messages = [message]
    uid = message.chat.id
    spent, why = usage.bot_try_spend(uid, "photo")
    if not spent:
        await message.answer(why, parse_mode=None)
        return
    status = await message.answer("📷 Смотрю, что на фото…")
    try:
        images = [got for m in messages[:5] if (got := await _photo_bytes(m))]
        if not images:
            raise vision.VisionError("не картинка")
        items = await vision.photo_items(images)
    except Exception as e:  # noqa: BLE001 — VisionError или сбой скачивания
        usage.unlog(spent)
        if not isinstance(e, vision.VisionError):
            logger.exception("фото")
        await status.edit_text("😔 Не получилось разобрать фото. Попробуй ещё раз — поближе и при хорошем свете.")
        return
    if not items:
        usage.unlog(spent)
        await status.edit_text("🤔 Не вижу на фото продуктов. Попробуй сфотографировать поближе.")
        return
    usage.event("photo_" + ("shelf" if mode == "shelf" else "recipe"), uid)
    _prune()
    key = (uid, status.message_id)
    _picks[key] = {"mode": mode, "items": items, "on": set(range(len(items))), "ts": time.time()}
    await status.edit_text(_pick_text(_picks[key]), reply_markup=_pick_kb(key))


@router.message(ProfileForm.shelf_add, IS_IMAGE)
async def shelf_photo(message: Message, state: FSMContext):
    if not plans.feature_on(message.chat.id, "photo_shelf"):
        await message.answer("Пока можно добавить только текстом: напиши продукты через запятую.")
        return
    await state.clear()
    await _recognize(message, state, "shelf")


@router.message(RecipeForm.preferred, IS_IMAGE)
async def form_photo(message: Message, state: FSMContext):
    if not plans.feature_on(message.chat.id, "photo_recipe"):
        await message.answer("Напиши продукты текстом — например: курица, рис, брокколи.")
        return
    await _recognize(message, state, "form")


@router.message(IS_IMAGE)       # в любом другом состоянии (в том числе сразу после меню) — фото холодильника
async def any_photo(message: Message, state: FSMContext):
    uid = message.chat.id
    if plans.feature_on(uid, "photo_recipe"):
        await _recognize(message, state, "new")
    elif plans.feature_on(uid, "photo_shelf") and plans.has_feature(uid, "memory"):
        await _recognize(message, state, "shelf")


def _pick_of(callback: CallbackQuery) -> tuple[tuple[int, int], dict] | None:
    key = (callback.message.chat.id, callback.message.message_id)
    p = _picks.get(key)
    return (key, p) if p else None


@router.callback_query(F.data.startswith("vp:t:"))
async def pick_toggle(callback: CallbackQuery):
    got = _pick_of(callback)
    if not got:
        await callback.answer("Фото устарело — пришли его ещё раз", show_alert=True)
        return
    key, p = got
    try:
        i = int(callback.data.split(":")[2])
    except ValueError:
        await callback.answer()
        return
    p["on"].symmetric_difference_update({i})
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=_pick_kb(key))


@router.callback_query(F.data == "vp:no")
async def pick_cancel(callback: CallbackQuery, state: FSMContext):
    _picks.pop((callback.message.chat.id, callback.message.message_id), None)
    if await state.get_state() == VisionForm.extra.state:
        await state.clear()
    await callback.answer("Отменил")
    await callback.message.edit_reply_markup()


@router.callback_query(F.data == "vp:add")
async def pick_add(callback: CallbackQuery, state: FSMContext):
    got = _pick_of(callback)
    if not got:
        await callback.answer("Фото устарело — пришли его ещё раз", show_alert=True)
        return
    key, p = got
    keep = {"vp_key": list(key)}
    if p["mode"] == "form":
        keep["vp_form"] = await state.get_data()     # вернёмся в анкету с её ответами
    await state.set_state(VisionForm.extra)
    await state.update_data(**keep)
    await callback.answer()
    await callback.message.answer("✏️ Напиши, что добавить, через запятую — например: «сметана, укроп».")


@router.message(VisionForm.extra, F.text)
async def pick_add_text(message: Message, state: FSMContext):
    data = await state.get_data()
    key = tuple(data.get("vp_key") or ())
    p = _picks.get(key)
    if p is None:
        await state.clear()
        await message.answer("Фото устарело — пришли его ещё раз.")
        return
    for name in vision._clean([x for x in message.text.split(",")]):
        if name not in p["items"] and len(p["items"]) < 40:
            p["items"].append(name)
            p["on"].add(len(p["items"]) - 1)
    if p["mode"] == "form":
        await state.set_state(RecipeForm.preferred)
        await state.set_data(data.get("vp_form") or {})
    else:
        await state.clear()
    try:
        await message.bot.edit_message_reply_markup(chat_id=key[0], message_id=key[1], reply_markup=None)
    except Exception:  # noqa: BLE001 — старое сообщение могло быть удалено
        pass
    sent = await message.answer(_pick_text(p))
    new_key = (message.chat.id, sent.message_id)      # список с галочками переезжает в новое сообщение
    _picks[new_key] = _picks.pop(key)
    await sent.edit_reply_markup(reply_markup=_pick_kb(new_key))


@router.callback_query(F.data == "vp:go")
async def pick_go(callback: CallbackQuery, state: FSMContext):
    key = (callback.message.chat.id, callback.message.message_id)
    p = _picks.pop(key, None)
    if not p:
        await callback.answer("Фото устарело — пришли его ещё раз", show_alert=True)
        return
    chosen = [n for i, n in enumerate(p["items"]) if i in p["on"]]
    if not chosen:
        _picks[key] = p
        await callback.answer("Отметь хотя бы один продукт", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_reply_markup()
    uid = callback.message.chat.id
    if p["mode"] == "shelf":
        for name in chosen:
            accounts.shelf_add(uid, name)
        text, markup = hooks["shelf_view"](uid)
        await callback.message.answer(f"🏠 Добавил на полку: {', '.join(chosen)}\n\n" + text, reply_markup=markup)
        return
    preferred = ", ".join(chosen)
    if p["mode"] == "form" and await state.get_state() == RecipeForm.preferred.state:
        await hooks["set_preferred"](callback.message, state, preferred)
    else:
        await hooks["start_recipe"](callback.message, state, preferred)
