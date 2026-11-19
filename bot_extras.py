"""
Три отдельные возможности бота: пересчёт порций под рецептом (без ИИ), общий список покупок из нескольких рецептов избранного,
проверка состава по фото упаковки (те же лимиты, что на сайте).
Роутер подключается раньше bot_vision: там любое фото считается фотографией холодильника.
"""
import asyncio
import logging
from urllib.parse import quote

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import accounts
import bot_profile
import bot_shop
import favorites
import recipe_math
import shoplists
import usage
import vision
from bot_vision import ALBUM_WAIT, IS_IMAGE, _photo_bytes
from last_recipe import get_last_recipe, set_last_recipe
from states import MyListForm

logger = logging.getLogger(__name__)
router = Router(name="extras")


# ---------- порции под рецептом ----------

def _ps_key(msg: Message) -> str:
    return f"ps:{msg.chat.id}:{msg.message_id}"


def portions_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="👥 Число порций", callback_data="ps:o")]


def add_portions(markup: InlineKeyboardMarkup, text: str) -> InlineKeyboardMarkup:
    """Кнопка над нижним рядом, если в рецепте указано число порций."""
    if recipe_math.base_servings(text):
        markup.inline_keyboard.insert(max(len(markup.inline_keyboard) - 1, 0), portions_row())
    return markup


def _ps_kb(cur: int | None) -> InlineKeyboardMarkup:
    nums = sorted(set(recipe_math.SERVINGS) | ({cur} if cur else set()))
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=("• " if n == cur else "") + str(n), callback_data=f"ps:s:{n}") for n in nums],
                                                 [InlineKeyboardButton(text="⬅️ Свернуть", callback_data="ps:c")]])


@router.callback_query(F.data == "ps:o")
async def portions_open(callback: CallbackQuery):
    msg = callback.message
    html = msg.html_text
    cur = recipe_math.base_servings(html)
    if not cur:
        await callback.answer("В этом рецепте не указано число порций", show_alert=True)
        return
    if not accounts.sess_get(_ps_key(msg)):
        s = {"html": html, "kb": msg.reply_markup.model_dump(exclude_none=True) if msg.reply_markup else None}
        last = get_last_recipe(msg.chat.id)
        if last and last.get("title") and last["title"] in (msg.text or ""):
            s["last"] = last["text"]       # этот рецепт и есть «последний»: пересчёт дойдёт до PDF, избранного и готовки
        accounts.sess_set(_ps_key(msg), s)
    await callback.answer("На сколько порций пересчитать?")
    await msg.edit_reply_markup(reply_markup=_ps_kb(cur))


@router.callback_query(F.data.startswith("ps:s:"))
async def portions_set(callback: CallbackQuery):
    msg = callback.message
    s = accounts.sess_get(_ps_key(msg))
    n = callback.data.split(":")[2]
    if not s or not n.isdigit():
        await callback.answer("Откройте рецепт заново, он устарел", show_alert=True)
        return
    n = int(n)
    new = recipe_math.scale_text(s["html"], n)
    if new == msg.html_text:
        await callback.answer()
        return
    try:
        await msg.edit_text(new, parse_mode="HTML", reply_markup=_ps_kb(n), disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            logger.warning("порции: %s", e)
            await callback.answer("Не получилось обновить сообщение", show_alert=True)
            return
    if s.get("last"):
        last = get_last_recipe(msg.chat.id)
        if last:
            set_last_recipe(msg.chat.id, last["title"], recipe_math.scale_text(s["last"], n), last.get("cuisine"))
    sl_key = bot_shop._key(msg.chat.id, msg.message_id)
    sl = accounts.sess_get(sl_key)
    if sl:                                 # открытый список покупок считал количества по старому числу порций
        sl.pop("am", None)
        sl.pop("share", None)
        accounts.sess_set(sl_key, sl)
    usage.event("portions", msg.chat.id)
    await callback.answer(f"Пересчитано на {n} порц.")


@router.callback_query(F.data == "ps:c")
async def portions_close(callback: CallbackQuery):
    s = accounts.sess_get(_ps_key(callback.message)) or {}
    await callback.answer()
    markup = InlineKeyboardMarkup.model_validate(s["kb"]) if s.get("kb") else None
    await callback.message.edit_reply_markup(reply_markup=markup)


# ---------- поделиться рецептом ----------

def add_share(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """«📤 Поделиться» в ряд с PDF (или отдельной строкой перед последней)."""
    btn = InlineKeyboardButton(text="📤 Поделиться", callback_data="rs:mk")
    for row in markup.inline_keyboard:
        if any((x.callback_data or "") == "pdf:r" for x in row) and len(row) < 3:
            row.append(btn)
            return markup
    markup.inline_keyboard.insert(max(len(markup.inline_keyboard) - 1, 0), [btn])
    return markup


@router.callback_query(F.data == "rs:mk")
async def share_recipe(callback: CallbackQuery):
    import recipe_share
    msg = callback.message
    try:
        rid = recipe_share.create(msg.chat.id, "", msg.text or msg.caption or "")
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
        return
    link = recipe_share.url(rid)
    usage.event("recipe_share", msg.chat.id)
    await callback.answer()
    title = recipe_share.get(rid)["title"]
    rows = [[InlineKeyboardButton(text="🔗 Открыть", url=link)],
            [InlineKeyboardButton(text="📤 Переслать", url=f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(title, safe='')}")]]
    await msg.reply(f"📤 «{title}» по ссылке: продукты с галочками и количеством, где купить, шаги с таймерами, пересчёт порций. "
                    f"Открывается в любом браузере, без бота и VPN, оттуда рецепт можно сохранить в избранное. Ссылка работает 30 дней.\n\n{link}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
                    parse_mode=None, disable_web_page_preview=True)


# ---------- общий список покупок из избранного ----------

COMBO_PAGE = 8


def _combo_key(uid) -> str:
    return f"combo:{uid}"


def add_combo(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """«➕ В общий список» под рецептом: рецепт копится в общем списке покупок (одном на бота и сайт).
    В одной строке с порциями и со своим значком: рядом уже есть «🛒 Продукты и магазины», две корзины путались."""
    btn = InlineKeyboardButton(text="➕ В общий список", callback_data="cb:add")
    for row in markup.inline_keyboard:
        if any((x.callback_data or "") == "ps:o" for x in row) and len(row) < 2:
            row.append(btn)
            return markup
    markup.inline_keyboard.insert(max(len(markup.inline_keyboard) - 1, 0), [btn])
    return markup


@router.callback_query(F.data == "cb:add")
async def combo_add(callback: CallbackQuery):
    from last_recipe import get_last_recipe
    from shopping import extract_dish_title
    msg = callback.message
    uid = msg.chat.id
    text = msg.text or msg.caption or ""
    title = extract_dish_title(text) or "Рецепт"
    last = get_last_recipe(uid) or {}
    cuisine = ""
    if last.get("text") and extract_dish_title(last["text"]) == title:
        text, cuisine = last["text"], last.get("cuisine") or ""     # с разметкой, как при обычном сохранении
    try:
        _, n = shoplists.combo_add(uid, title, text, cuisine)
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
        return
    usage.event("combo_add", uid)
    await callback.answer(f"🛒 «{title[:40]}» в общем списке. Рецептов в списке: {n}")
    mk = msg.reply_markup
    if mk:
        rows = [[InlineKeyboardButton(text=f"✅ В списке: {n}", callback_data="cb:o") if b.callback_data == "cb:add" else b
                 for b in row] for row in mk.inline_keyboard]
        try:
            await msg.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        except TelegramBadRequest:
            pass


def _combo_view(uid) -> tuple[str, InlineKeyboardMarkup]:
    s = shoplists.combo_get(uid)
    favs = list(reversed(favorites.get_favorites(uid)))
    pages = max(1, -(-len(favs) // COMBO_PAGE))
    page = max(0, min(s.get("page", 0), pages - 1))
    rows = [[InlineKeyboardButton(text=("✅ " if f["id"] in s["ids"] else "▫️ ") + f["title"][:38], callback_data=f"cb:t:{f['id']}")]
            for f in favs[page * COMBO_PAGE:(page + 1) * COMBO_PAGE]]
    if pages > 1:
        nav = ([InlineKeyboardButton(text="◀️", callback_data=f"cb:p:{page - 1}")] if page else [])
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="bk:noop"))
        nav += ([InlineKeyboardButton(text="▶️", callback_data=f"cb:p:{page + 1}")] if page < pages - 1 else [])
        rows.append(nav)
    n = len(s["ids"])
    if n:
        rows.append([InlineKeyboardButton(text=f"🛒 Собрать список ({n})", callback_data="cb:go"),
                     InlineKeyboardButton(text="🧹 Очистить", callback_data="cb:clr")])
    rows.append([InlineKeyboardButton(text="⬅️ К избранному", callback_data="bk:fav:0")])
    if not favs:
        return ("🛒 Общий список покупок\n\nПока пусто. Под любым рецептом (из книги, подбора или избранного) нажмите "
                "«🛒 В общий список покупок» — рецепты накопятся здесь, а одинаковые продукты сложатся."), InlineKeyboardMarkup(inline_keyboard=rows)
    return ("🛒 Общий список покупок\n\nОтметьте рецепты, которые будете готовить (или нажимайте «🛒 В общий список» под рецептами). "
            "Одинаковые продукты сложатся."), InlineKeyboardMarkup(inline_keyboard=rows)


async def _combo_redraw(callback: CallbackQuery) -> None:
    text, markup = _combo_view(callback.message.chat.id)
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode=None)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            await callback.message.answer(text, reply_markup=markup, parse_mode=None)


@router.callback_query(F.data == "cb:o")
async def combo_open(callback: CallbackQuery):
    await callback.answer()        # отмеченное раньше (в боте или на сайте) сохраняется
    if not (callback.message.text or "").lstrip().startswith("🍽"):
        await _combo_redraw(callback)
    else:                          # нажали под рецептом — рецепт не затираем, список присылаем отдельно
        text, markup = _combo_view(callback.message.chat.id)
        await callback.message.answer(text, reply_markup=markup, parse_mode=None)


@router.callback_query(F.data == "cb:clr")
async def combo_clear(callback: CallbackQuery):
    shoplists.combo_clear(callback.message.chat.id)
    await callback.answer("Список очищен")
    await _combo_redraw(callback)


@router.callback_query(F.data.startswith("cb:t:"))
async def combo_toggle(callback: CallbackQuery):
    uid = callback.message.chat.id
    fid = callback.data.split(":", 2)[2]
    s = shoplists.combo_get(uid)
    s["ids"] = [x for x in s["ids"] if x != fid] if fid in s["ids"] else (s["ids"] + [fid])[:shoplists.MAX_RECIPES]
    shoplists.combo_save(uid, s)
    await callback.answer()
    await _combo_redraw(callback)


@router.callback_query(F.data.startswith("cb:p:"))
async def combo_page(callback: CallbackQuery):
    uid = callback.message.chat.id
    s = shoplists.combo_get(uid)
    p = callback.data.split(":")[2]
    s["page"] = int(p) if p.isdigit() else 0
    shoplists.combo_save(uid, s)
    await callback.answer()
    await _combo_redraw(callback)


@router.callback_query(F.data == "cb:go")
async def combo_go(callback: CallbackQuery):
    uid = callback.message.chat.id
    s = shoplists.combo_get(uid)
    try:
        res = shoplists.from_favorites(uid, s["ids"])
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
        return
    shoplists.combo_clear(uid)          # список собран — следующий начнём с чистого
    usage.event("list_combo", uid)
    await callback.answer()
    link = res["url"]
    lines = [f"🛒 {res['title']}", ""] + [f"▫️ {it['name']}" + (f" — {it['amount']}" if it["amount"] else "") for it in res["items"]]
    if res["home"]:
        lines += ["", "🏠 Есть дома: " + ", ".join(res["home"])]
    lines += ["", f"☑️ Отмечать галочками, можно вместе (без VPN и входа): {link}"]
    rows = [[InlineKeyboardButton(text="☑️ Открыть с галочками", url=link)],
            [InlineKeyboardButton(text="📤 Переслать", url=f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(res['title'], safe='')}")]]
    await callback.message.answer("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode=None, disable_web_page_preview=True)


# ---------- свой список покупок (подписка) ----------

def _mylist_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Отмена", callback_data="ml:x")]])


def mylist_kb(lid: str, link: str, title: str) -> InlineKeyboardMarkup:
    """Под присланным списком: открыть с галочками и переслать — в одну строку, магазины — отдельно (их нажимают чаще)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="☑️ Открыть с галочками", url=link),
         InlineKeyboardButton(text="📤", url=f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(title, safe='')}")],
        [InlineKeyboardButton(text="🛒 Найти в магазине", callback_data=f"ml:sh:{lid}")],
    ])


@router.callback_query(F.data == "ml:o")
async def mylist_open(callback: CallbackQuery, state: FSMContext):
    import plans
    uid = callback.message.chat.id
    if not plans.has_feature(uid, "mylist"):
        usage.event("gate_mylist", uid)
        await callback.answer()
        await callback.message.answer(
            "💎 Свой список покупок — в подписке «Повар».\n\nПишете или говорите голосом, что купить («молоко 2 л, хлеб, яйца 10 шт») — "
            "бот присылает список с галочками: его можно переслать, отмечать вместе и искать продукты в магазинах.",
            reply_markup=bot_profile.upsell_kb(uid), parse_mode=None)
        return
    await callback.answer()
    await state.set_state(MyListForm.items)
    await callback.message.answer(
        "📝 Что купить? Напишите одним сообщением или скажите голосом — через запятую или по строке, можно с количеством:\n"
        "«молоко 2 л, хлеб, яйца 10 шт, бумажные полотенца»", reply_markup=_mylist_cancel(), parse_mode=None)


@router.callback_query(F.data == "ml:x")
async def mylist_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Отменено")
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass


@router.message(MyListForm.items, F.text, ~F.text.startswith("/"))
async def mylist_text(message: Message, state: FSMContext):
    uid = message.chat.id
    try:
        res = shoplists.from_text(uid, message.text)
    except ValueError as e:
        await message.answer(f"😔 {e}", reply_markup=_mylist_cancel(), parse_mode=None)
        return
    await state.clear()
    usage.event("mylist", uid)
    lines = [f"📝 {res['title']}", ""] + [f"▫️ {it['name']}" + (f" — {it['amount']}" if it["amount"] else "") for it in res["items"]]
    lines += ["", "Отмечайте галочками по ссылке — можно вместе, без бота и VPN."]
    await message.answer("\n".join(lines)[:4000], reply_markup=mylist_kb(res["id"], res["url"], res["title"]), parse_mode=None,
                         disable_web_page_preview=True)


@router.callback_query(F.data.startswith("ml:sh:"))
async def mylist_shops(callback: CallbackQuery):
    """Ссылки на поиск каждого продукта списка в магазинах, которые человек выбрал в настройках."""
    from shopping import format_shopping_message
    rec = shoplists.get(callback.data.split(":", 2)[2])
    if not rec:
        await callback.answer("Список устарел — составьте новый", show_alert=True)
        return
    done = set(rec["done"])
    names = [it["name"] for i, it in enumerate(rec["items"]) if i not in done] or [it["name"] for it in rec["items"]]
    await callback.answer()
    await callback.message.answer(format_shopping_message(names, callback.message.chat.id), disable_web_page_preview=True)


# ---------- проверка состава ----------

class LabelForm(StatesGroup):
    wait = State()


_label_albums: dict[str, list[Message]] = {}
LABEL_NOTE = "ИИ читает этикетку и может ошибаться. Сверяйтесь с упаковкой, особенно при аллергии. Это не медицинский совет."


def label_text(res: dict) -> str:
    """Ответ модели как текст сообщения: только то, что есть на упаковке."""
    lines = []
    if res.get("compare"):
        lines += ["⚖️ Сравнение", res["compare"], ""]
    for p in res["products"]:
        lines.append("🔬 " + (p["name"] or "Продукт"))
        for title, key in (("Состав", "ingredients"), ("Добавки", "additives"), ("На что обратить внимание", "attention")):
            if p[key]:
                lines.append(f"{title}: " + "; ".join(p[key]))
        lines.append("")
    return "\n".join(lines + [LABEL_NOTE])[:4000]


async def _label_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(LabelForm.wait)
    await message.answer("🔬 Проверка состава\n\nСфотографируйте состав на упаковке и пришлите фото. Чтобы сравнить два продукта, пришлите два фото одним сообщением.",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="lb:x")]]), parse_mode=None)


@router.message(Command("label"))
async def label_command(message: Message, state: FSMContext):
    await _label_prompt(message, state)


@router.callback_query(F.data == "lb:o")
async def label_open(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await _label_prompt(callback.message, state)


@router.callback_query(F.data == "lb:x")
async def label_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.edit_reply_markup()


@router.message(LabelForm.wait, IS_IMAGE)
async def label_photo(message: Message, state: FSMContext):
    group = message.media_group_id
    if group:
        if group in _label_albums:
            _label_albums[group].append(message)
            return
        _label_albums[group] = [message]
        await asyncio.sleep(ALBUM_WAIT)
        messages = _label_albums.pop(group, [message])
    else:
        messages = [message]
    uid = message.chat.id
    spent, why = usage.bot_try_spend(uid, "label")
    if not spent:
        await state.clear()
        await message.answer(why, parse_mode=None, reply_markup=bot_profile.upsell_kb(uid))
        return
    if why:
        await message.answer(why, parse_mode=None)
    await state.clear()
    status = await message.answer("🔬 Читаю состав…")
    try:
        images = [got for m in messages[:2] if (got := await _photo_bytes(m))]
        if not images:
            raise vision.VisionError("не картинка")
        res = await vision.check_label(images)
    except Exception as e:  # noqa: BLE001 — VisionError или сбой скачивания
        usage.unlog(spent)
        if not isinstance(e, vision.VisionError):
            logger.exception("состав")
        await status.edit_text("😔 Не получилось прочитать состав, попробуйте ещё раз.", parse_mode=None)
        return
    if not res["products"]:      # запрос к модели уже сделан: попытка засчитана
        await status.edit_text("🤔 Не вижу состав на фото: снимите ближе, чтобы текст был резким.", parse_mode=None)
        return
    usage.event("label_check", uid)
    await status.edit_text(label_text(res), parse_mode=None)


@router.message(LabelForm.wait, F.text, ~F.text.startswith("/"))
async def label_not_photo(message: Message):
    await message.answer("Нужна фотография состава на упаковке. Или нажмите «❌ Отмена».", parse_mode=None)
