"""
Список покупок с галочками — разворачивается на месте (вместо кнопок под рецептом / меню / корзиной плана),
без новых сообщений; «⬅️ Свернуть» возвращает прежние кнопки.

Сверху — что отмечаем: ✅ купил, ❌ нет в магазине, 🏠 есть дома. Нажатие на продукт ставит выбранную отметку.
Долгоживущее (масло, специи, крупы), отмеченное «купил» или «есть дома», уходит на полку — в следующих
рецептах оно сразу стоит отдельно как «🏠 есть дома». Внизу: найти оставшееся в магазине (одно сообщение со
ссылками, которое переключается между магазинами и закрывается), заменить отсутствующие (ИИ), отправить списком.
"""
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import accounts
import plans
from shopping import format_store_links
from stores.links import DEFAULT_STORES, STORES

logger = logging.getLogger(__name__)
router = Router(name="shop")

# Заполняет bot.py (там данные рецептов/меню/плана): terms(message, ctx), amounts(message, ctx),
# orig_kb(message, ctx) -> InlineKeyboardMarkup, run_missing(callback, anchor, ctx, names)
hooks: dict = {}

MODES = {"b": "✅ Купил", "m": "❌ Нет", "h": "🏠 Есть дома"}
ICON = {"b": "✅", "m": "❌", "h": "🏠"}
PAGE = 20


def base_ctx(ctx: str) -> str:
    """f<id> — рецепт из избранного: для продуктов, замены и корзины это обычный рецепт."""
    return "r" if ctx.startswith("f") else ctx


def _key(chat_id: int, msg_id: int) -> str:
    return f"sl:{chat_id}:{msg_id}"


def _order(s: dict) -> list[int]:
    """Сначала то, что нужно купить, потом «есть дома» — отдельным списком."""
    n = len(s["terms"])
    st = s["st"]
    return [i for i in range(n) if st.get(str(i)) != "h"] + [i for i in range(n) if st.get(str(i)) == "h"]


def checklist_kb(s: dict) -> InlineKeyboardMarkup:
    st, mode = s["st"], s.get("mode", "b")
    rows = [[InlineKeyboardButton(text=("• " if k == mode else "") + label, callback_data=f"sl:m:{k}")
             for k, label in MODES.items()]]
    order = _order(s)
    page = max(0, min(s.get("page", 0), (len(order) - 1) // PAGE if order else 0))
    chunk = order[page * PAGE:(page + 1) * PAGE]
    row, home_header = [], False
    for i in chunk:
        code = st.get(str(i))
        if code == "h" and not home_header:
            if row:
                rows.append(row)
                row = []
            rows.append([InlineKeyboardButton(text="— 🏠 есть дома / на полке —", callback_data="sl:n")])
            home_header = True
        row.append(InlineKeyboardButton(text=f"{ICON.get(code, '▫️')} {s['terms'][i][:22]}", callback_data=f"sl:i:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if len(order) > PAGE:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀", callback_data=f"sl:p:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{(len(order) - 1) // PAGE + 1}", callback_data="sl:n"))
        if (page + 1) * PAGE < len(order):
            nav.append(InlineKeyboardButton(text="▶", callback_data=f"sl:p:{page + 1}"))
        rows.append(nav)
    missing_n = sum(1 for v in st.values() if v == "m")
    bottom = [InlineKeyboardButton(text="🏪 Найти в магазине", callback_data="sl:st")]
    if missing_n and base_ctx(s["ctx"]) in ("r", "d", "w"):
        bottom.append(InlineKeyboardButton(text=f"🔄 Заменить ❌ ({missing_n})", callback_data="sl:rp"))
    rows.append(bottom)
    rows.append([InlineKeyboardButton(text="📤 Списком", callback_data="sl:tx"),
                 InlineKeyboardButton(text="⬅️ Свернуть", callback_data="sl:c")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _remaining(s: dict) -> list[str]:
    return [t for i, t in enumerate(s["terms"]) if s["st"].get(str(i)) not in ("b", "h")]


async def _load(callback: CallbackQuery, msg: Message | None = None) -> dict | None:
    msg = msg or callback.message
    s = accounts.sess_get(_key(msg.chat.id, msg.message_id))
    if not s:
        await callback.answer("Список устарел — открой его заново кнопкой «🛒 Список покупок».", show_alert=True)
    return s


def _save(msg: Message, s: dict):
    accounts.sess_set(_key(msg.chat.id, msg.message_id), s)


async def _redraw(callback: CallbackQuery, s: dict):
    _save(callback.message, s)
    try:
        await callback.message.edit_reply_markup(reply_markup=checklist_kb(s))
    except TelegramRetryAfter:
        pass          # слишком быстро нажимают — отметка сохранена, нарисуется со следующим нажатием
    except Exception as e:  # noqa: BLE001
        if "not modified" not in str(e):
            raise


@router.callback_query(F.data.startswith("sl:o:"))
async def list_open(callback: CallbackQuery):
    ctx = callback.data.split(":", 2)[2]
    msg = callback.message
    terms = hooks["terms"](msg, base_ctx(ctx))
    if not terms:
        await callback.answer("Не нашёл список продуктов — открой рецепт заново.", show_alert=True)
        return
    old = accounts.sess_get(_key(msg.chat.id, msg.message_id))
    if old and old.get("terms") == terms:
        s = old                            # уже отмечал — отметки сохраняются
    else:
        st = {}
        if plans.has_feature(msg.chat.id, "memory"):
            st = {str(i): "h" for i in accounts.shelf_match(msg.chat.id, terms)}
        s = {"ctx": ctx, "terms": terms, "st": st, "mode": "b", "page": 0}
    await callback.answer("Выбери отметку сверху и нажимай на продукты")
    await _redraw(callback, s)


@router.callback_query(F.data.startswith("sl:m:"))
async def list_mode(callback: CallbackQuery):
    s = await _load(callback)
    if not s:
        return
    mode = callback.data.split(":")[2]
    s["mode"] = mode if mode in MODES else "b"
    await callback.answer({"b": "Отмечаю купленное", "m": "Отмечаю, чего нет в магазине",
                           "h": "Отмечаю, что уже есть дома"}[s["mode"]])
    await _redraw(callback, s)


@router.callback_query(F.data.startswith("sl:i:"))
async def list_item(callback: CallbackQuery):
    s = await _load(callback)
    if not s:
        return
    i = callback.data.split(":")[2]
    if not i.isdigit() or int(i) >= len(s["terms"]):
        await callback.answer()
        return
    mode, cur = s.get("mode", "b"), s["st"].get(i)
    if cur == mode:
        s["st"].pop(i, None)
    else:
        s["st"][i] = mode
        term = s["terms"][int(i)]
        if mode in ("b", "h") and plans.has_feature(callback.message.chat.id, "memory") and accounts.shelf_life_days(term):
            accounts.shelf_add(callback.message.chat.id, term)
    await callback.answer()
    await _redraw(callback, s)


@router.callback_query(F.data.startswith("sl:p:"))
async def list_page(callback: CallbackQuery):
    s = await _load(callback)
    if not s:
        return
    s["page"] = int(callback.data.split(":")[2])
    await callback.answer()
    await _redraw(callback, s)


@router.callback_query(F.data == "sl:n")
async def list_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "sl:c")
async def list_collapse(callback: CallbackQuery):
    s = accounts.sess_get(_key(callback.message.chat.id, callback.message.message_id)) or {}
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=hooks["orig_kb"](callback.message, s.get("ctx", "r")))


# ---------- магазин: одно сообщение-ответ, переключается между магазинами ----------

def _store_kb(ctx: str, current: str | None) -> InlineKeyboardMarkup:
    rows, row = [], []
    for key in DEFAULT_STORES:
        if key in STORES:
            row.append(InlineKeyboardButton(text=("✅ " if key == current else "") + STORES[key][0],
                                            callback_data=f"sl:s:{key}"))
            if len(row) == 2:
                rows.append(row)
                row = []
    if row:
        rows.append(row)
    if current == "vkusvill" and base_ctx(ctx) in ("r", "d", "w"):
        rows.append([InlineKeyboardButton(text="🧺 Собрать корзину во ВкусВилле (цены)", callback_data=f"shop:cart:{ctx}")])
    rows.append([InlineKeyboardButton(text="✖ Закрыть", callback_data="sl:sx")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "sl:st")
async def list_store(callback: CallbackQuery):
    s = await _load(callback)
    if not s:
        return
    await callback.answer()
    text = "🏪 В каком магазине искать? Покажу только то, что ещё не куплено и не стоит «есть дома»."
    mid = s.get("store_msg")
    if mid:
        try:
            await callback.bot.edit_message_text(text, chat_id=callback.message.chat.id, message_id=mid,
                                                 reply_markup=_store_kb(base_ctx(s["ctx"]), None), parse_mode=None)
            return
        except Exception:  # noqa: BLE001 — сообщение удалили — пришлём новое
            pass
    sent = await callback.message.reply(text, reply_markup=_store_kb(base_ctx(s["ctx"]), None), parse_mode=None)
    s["store_msg"] = sent.message_id
    _save(callback.message, s)


@router.callback_query(F.data.startswith("sl:s:"))
async def list_store_pick(callback: CallbackQuery):
    parent = callback.message.reply_to_message
    store = callback.data.split(":")[2]
    if not parent or store not in DEFAULT_STORES:
        await callback.answer("Открой список покупок заново.", show_alert=True)
        return
    s = await _load(callback, parent)
    if not s:
        return
    rest = _remaining(s)
    if not rest:
        await callback.answer("Всё уже куплено или есть дома 🎉", show_alert=True)
        return
    await callback.answer()
    text = format_store_links(rest, store, hooks["amounts"](parent, base_ctx(s["ctx"])))
    home = [t for i, t in enumerate(s["terms"]) if s["st"].get(str(i)) == "h"]
    if home:
        text += "\n\n🏠 Не включил — есть дома: " + ", ".join(home)
    try:
        await callback.message.edit_text(text, reply_markup=_store_kb(base_ctx(s["ctx"]), store), disable_web_page_preview=True)
    except Exception as e:  # noqa: BLE001
        if "not modified" in str(e):
            return
        await callback.message.edit_text(text, reply_markup=_store_kb(base_ctx(s["ctx"]), store), disable_web_page_preview=True,
                                         parse_mode=None)


@router.callback_query(F.data.in_({"sl:sx", "sl:tx:x"}))
async def list_close_msg(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:  # noqa: BLE001
        await callback.message.edit_reply_markup()


# ---------- списком (переслать) и замена отсутствующих ----------

@router.callback_query(F.data == "sl:tx")
async def list_text(callback: CallbackQuery):
    s = await _load(callback)
    if not s:
        return
    await callback.answer()
    st = s["st"]
    lines = ["🛒 Список покупок", ""]
    for i, t in enumerate(s["terms"]):
        code = st.get(str(i))
        if code != "h":
            lines.append(f"{'✅' if code == 'b' else '❌' if code == 'm' else '▫️'} {t}")
    home = [t for i, t in enumerate(s["terms"]) if st.get(str(i)) == "h"]
    if home:
        lines += ["", "🏠 Есть дома: " + ", ".join(home)]
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖ Убрать", callback_data="sl:tx:x")]])
    await callback.message.answer("\n".join(lines), reply_markup=kb, parse_mode=None)


@router.callback_query(F.data == "sl:rp")
async def list_replace(callback: CallbackQuery):
    s = await _load(callback)
    if not s:
        return
    names = [t for i, t in enumerate(s["terms"]) if s["st"].get(str(i)) == "m"]
    if not names:
        await callback.answer("Отметь ❌ продукты, которых нет в магазине.", show_alert=True)
        return
    await hooks["run_missing"](callback, callback.message, base_ctx(s["ctx"]), names)
