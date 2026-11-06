"""
«Продукты и магазины» разворачиваются на месте (вместо кнопок под рецептом / меню / корзиной плана), без новых сообщений;
«⬅️ Свернуть» возвращает прежние кнопки. Внутри: отмечать купленное, найти в магазине, отправить список.

Галочки открываются только по кнопке, потому что список длинный. Сверху — что отмечаем: ✅ купил, ❌ нет в магазине, 🏠 есть дома.
Долгоживущее (масло, специи, крупы), отмеченное «купил» или «есть дома», уходит на полку — в следующих рецептах оно сразу стоит
отдельно как «🏠 есть дома». «Отправить список» присылает текст с количеством и ссылку на страницу с галочками (shoplists.py):
пересланные кнопки у получателя не работают, а страница работает у всех и без VPN.
"""
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramRetryAfter
from urllib.parse import quote

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import accounts
import shoplists
import usage
import plans
from shopping import extract_dish_title, format_store_links, item_amounts
from stores import prefs
from stores.links import DEFAULT_STORES, STORES

logger = logging.getLogger(__name__)
router = Router(name="shop")

# Заполняет bot.py (там данные рецептов/меню/плана): terms(message, ctx), amounts(message, ctx),
# orig_kb(message, ctx) -> InlineKeyboardMarkup, run_missing(callback, anchor, ctx, names)
hooks: dict = {}

MODES = {"b": "✅ Куплено", "m": "❌ Нет", "h": "🏠 Есть дома"}
ICON = {"b": "✅", "m": "❌", "h": "🏠"}
PAGE = 12


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


def hub_kb(s: dict) -> InlineKeyboardMarkup:
    """Меню «Продукты и магазины»."""
    total = len(s["terms"])
    done = sum(1 for v in s["st"].values() if v in ("b", "h"))
    missing_n = sum(1 for v in s["st"].values() if v == "m")
    rows = [[InlineKeyboardButton(text=f"☑️ Отмечать купленное ({done} из {total})", callback_data="sl:chk")],
            [InlineKeyboardButton(text="🏪 Найти в магазине", callback_data="sl:st")],
            [InlineKeyboardButton(text="📤 Отправить список", callback_data="sl:tx")]]
    if missing_n and base_ctx(s["ctx"]) in ("r", "d", "w"):
        rows.append([InlineKeyboardButton(text=f"🔄 Заменить ❌ ({missing_n})", callback_data="sl:rp")])
    rows.append([InlineKeyboardButton(text="⬅️ Свернуть", callback_data="sl:c")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _label(s: dict, i: int) -> str:
    """Продукт с количеством на кнопке. Кнопка на всю ширину, поэтому количество видно."""
    am = (s.get("am") or [])
    amount = am[i] if i < len(am) else ""
    name = s["terms"][i]
    return (f"{name[:30]} · {amount}" if amount else name[:40])


def checklist_kb(s: dict) -> InlineKeyboardMarkup:
    st, mode = s["st"], s.get("mode", "b")
    rows = [[InlineKeyboardButton(text=("• " if k == mode else "") + label, callback_data=f"sl:m:{k}")
             for k, label in MODES.items()]]
    order = _order(s)
    page = max(0, min(s.get("page", 0), (len(order) - 1) // PAGE if order else 0))
    chunk = order[page * PAGE:(page + 1) * PAGE]
    home_header = False
    for i in chunk:
        code = st.get(str(i))
        if code == "h" and not home_header:
            rows.append([InlineKeyboardButton(text="— 🏠 есть дома / на полке —", callback_data="sl:n")])
            home_header = True
        rows.append([InlineKeyboardButton(text=f"{ICON.get(code, '▫️')} {_label(s, i)}", callback_data=f"sl:i:{i}")])
    if len(order) > PAGE:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀", callback_data=f"sl:p:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{(len(order) - 1) // PAGE + 1}", callback_data="sl:n"))
        if (page + 1) * PAGE < len(order):
            nav.append(InlineKeyboardButton(text="▶", callback_data=f"sl:p:{page + 1}"))
        rows.append(nav)
    missing_n = sum(1 for v in st.values() if v == "m")
    last = [InlineKeyboardButton(text="⬅️ Назад", callback_data="sl:h")]
    if missing_n and base_ctx(s["ctx"]) in ("r", "d", "w"):
        last.insert(0, InlineKeyboardButton(text=f"🔄 Заменить ❌ ({missing_n})", callback_data="sl:rp"))
    rows.append(last)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _remaining(s: dict) -> list[str]:
    return [t for i, t in enumerate(s["terms"]) if s["st"].get(str(i)) not in ("b", "h")]


async def _load(callback: CallbackQuery, msg: Message | None = None) -> dict | None:
    msg = msg or callback.message
    s = accounts.sess_get(_key(msg.chat.id, msg.message_id))
    if not s:
        await callback.answer("Список устарел — откройте его заново кнопкой «🛒 Продукты и магазины».", show_alert=True)
    return s


def _save(msg: Message, s: dict):
    accounts.sess_set(_key(msg.chat.id, msg.message_id), s)


def _recipe_text(msg: Message) -> str:
    for src in (msg, msg.reply_to_message):
        if src and "Продукты" in (src.text or "") or src and "Ингредиенты" in (src.text or ""):
            return src.text
    return ""


def _amounts(msg: Message, ctx: str, terms: list[str]) -> list[str]:
    """Сколько каждого продукта: из рецепта, меню или корзины плана."""
    base = base_ctx(ctx)
    return item_amounts(terms, hooks["amounts"](msg, base), _recipe_text(msg) if base == "r" else "")


async def _redraw(callback: CallbackQuery, s: dict, kb_fn=None):
    _save(callback.message, s)
    try:
        await callback.message.edit_reply_markup(reply_markup=(kb_fn or checklist_kb)(s))
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
        await callback.answer("Не нашёл список продуктов — откройте рецепт заново.", show_alert=True)
        return
    old = accounts.sess_get(_key(msg.chat.id, msg.message_id))
    if old and old.get("terms") == terms:
        s = old                            # уже отмечал — отметки сохраняются
    else:
        st = {}
        if plans.shelf_on(msg.chat.id):
            st = {str(i): "h" for i in accounts.shelf_match(msg.chat.id, terms)}
        s = {"ctx": ctx, "terms": terms, "st": st, "mode": "b", "page": 0}
    if "am" not in s:
        s["am"] = _amounts(msg, ctx, terms)
    usage.event("list_open", msg.chat.id)
    await callback.answer()
    await _redraw(callback, s, hub_kb)


@router.callback_query(F.data == "sl:h")
async def list_hub(callback: CallbackQuery):
    s = await _load(callback)
    if not s:
        return
    await callback.answer()
    await _redraw(callback, s, hub_kb)


@router.callback_query(F.data == "sl:chk")
async def list_check(callback: CallbackQuery):
    s = await _load(callback)
    if not s:
        return
    await callback.answer("Выберите отметку сверху и нажимайте на продукты")
    await _redraw(callback, s)


@router.callback_query(F.data.startswith("sl:m:"))
async def list_mode(callback: CallbackQuery):
    s = await _load(callback)
    if not s:
        return
    mode = callback.data.split(":")[2]
    s["mode"] = mode if mode in MODES else "b"
    _save(callback.message, s)
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
        if mode in ("b", "h") and plans.shelf_on(callback.message.chat.id) and accounts.shelf_life_days(term):
            accounts.shelf_add(callback.message.chat.id, term)
    _save(callback.message, s)
    await callback.answer()
    await _redraw(callback, s)


@router.callback_query(F.data.startswith("sl:p:"))
async def list_page(callback: CallbackQuery):
    s = await _load(callback)
    if not s:
        return
    s["page"] = int(callback.data.split(":")[2])
    _save(callback.message, s)
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

def _store_kb(ctx: str, current: str | None, uid: int | None = None) -> InlineKeyboardMarkup:
    rows, row = [], []
    for key in prefs.get_enabled(uid):
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
                                                 reply_markup=_store_kb(base_ctx(s["ctx"]), None, callback.message.chat.id), parse_mode=None)
            return
        except Exception:  # noqa: BLE001 — сообщение удалили — пришлём новое
            pass
    sent = await callback.message.reply(text, reply_markup=_store_kb(base_ctx(s["ctx"]), None, callback.message.chat.id), parse_mode=None)
    s["store_msg"] = sent.message_id
    _save(callback.message, s)


@router.callback_query(F.data.startswith("sl:s:"))
async def list_store_pick(callback: CallbackQuery):
    parent = callback.message.reply_to_message
    store = callback.data.split(":")[2]
    if not parent or store not in DEFAULT_STORES:
        await callback.answer("Откройте список покупок заново.", show_alert=True)
        return
    s = await _load(callback, parent)
    if not s:
        return
    rest = _remaining(s)
    usage.event("store_links", callback.message.chat.id)
    if not rest:
        await callback.answer("Всё уже куплено или есть дома", show_alert=True)
        return
    await callback.answer()
    text = format_store_links(rest, store, hooks["amounts"](parent, base_ctx(s["ctx"])))
    home = [t for i, t in enumerate(s["terms"]) if s["st"].get(str(i)) == "h"]
    if home:
        text += "\n\n🏠 Не включил — есть дома: " + ", ".join(home)
    try:
        await callback.message.edit_text(text, reply_markup=_store_kb(base_ctx(s["ctx"]), store, callback.message.chat.id), disable_web_page_preview=True)
    except Exception as e:  # noqa: BLE001
        if "not modified" in str(e):
            return
        await callback.message.edit_text(text, reply_markup=_store_kb(base_ctx(s["ctx"]), store, callback.message.chat.id), disable_web_page_preview=True,
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
    st, msg = s["st"], callback.message
    am = s.get("am") or []
    amount = lambda i: am[i] if i < len(am) else ""
    title = "Список покупок"
    text = _recipe_text(msg)
    if text and base_ctx(s["ctx"]) == "r":
        title = extract_dish_title(text)[:80] or title
    lines = [f"🛒 {title}", ""]
    items, done = [], []
    for i, t in enumerate(s["terms"]):
        code = st.get(str(i))
        if code == "h":
            continue
        lines.append(f"{'✅' if code == 'b' else '❌' if code == 'm' else '▫️'} {t}" + (f" — {amount(i)}" if amount(i) else ""))
        if code == "b":
            done.append(len(items))
        items.append({"name": t, "amount": amount(i)})
    home = [t for i, t in enumerate(s["terms"]) if st.get(str(i)) == "h"]
    if home:
        lines += ["", "🏠 Есть дома: " + ", ".join(home)]
    usage.event("list_share", msg.chat.id)
    rows = []
    try:
        lid = s.get("share") or shoplists.create(msg.chat.id, title, items, done)
        s["share"] = lid
        _save(msg, s)
        link = shoplists.url(lid)
        lines += ["", f"☑️ Отмечать галочками, можно вместе (без VPN и входа): {link}"]
        rows.append([InlineKeyboardButton(text="☑️ Открыть с галочками", url=link)])
        rows.append([InlineKeyboardButton(text="📤 Переслать", url=f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(title, safe='')}")])
    except ValueError as e:                      # слишком часто или пустой: текст всё равно пригодится
        lines += ["", str(e)]
    rows.append([InlineKeyboardButton(text="✖ Убрать", callback_data="sl:tx:x")])
    await msg.answer("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode=None, disable_web_page_preview=True)


@router.callback_query(F.data == "sl:rp")
async def list_replace(callback: CallbackQuery):
    s = await _load(callback)
    if not s:
        return
    names = [t for i, t in enumerate(s["terms"]) if s["st"].get(str(i)) == "m"]
    usage.event("list_replace", callback.message.chat.id)
    if not names:
        await callback.answer("Отметьте ❌ продукты, которых нет в магазине.", show_alert=True)
        return
    await hooks["run_missing"](callback, callback.message, base_ctx(s["ctx"]), names)
