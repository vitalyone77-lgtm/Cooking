"""
«📖 Книга рецептов» в боте: листать общую книгу (без лимитов, ИИ не вызывается), фильтры: раздел, на чём готовить, на сколько человек,
основной ингредиент, калории, время; хештеги и поиск по слову; своё избранное. Все переходы правят один экран (ui.py).
Новый рецепт под себя делается кнопкой «Создать»: сначала бот ищет в книге, а если подходящего нет, составляет новый.
"""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import accounts
import book
import cards
import favorites
import ui
import usage
from last_recipe import set_last_recipe
from states import BookForm

logger = logging.getLogger(__name__)
router = Router(name="book")
recipe_kb = None            # подставляет bot.py: клавиатура под рецептом (как после обычной генерации)
PAGE = 8

FIELDS = {
    "meal": ("🍽 Раздел", [("все", None)] + [(book.MEAL_TITLES[m], m) for m in book.MEALS]),
    "app": ("🔥 Техника", [("любая", None)] + [(v, k) for k, v in book.APPLIANCES.items()]),
    "srv": ("👥 Порции", [("любые", None)] + [(str(n), n) for n in (1, 2, 3, 4, 6)]),
    "main": ("🥩 Основа", [("любая", None)] + [(m.capitalize(), m) for m in book.MAINS]),
    "kcal": ("⚡ Калории", [("любые", None), ("до 300", 300), ("до 450", 450), ("до 600", 600)]),
    "time": ("⏱ Время", [("любое", None), ("до 15 мин", 15), ("до 30 мин", 30), ("до 60 мин", 60)]),
}
STATE_KEY = {"meal": "meal", "app": "appliance", "srv": "servings", "main": "main", "kcal": "max_kcal", "time": "max_min"}


def _key(uid) -> str:
    return f"bk:{uid}"


def _flt(uid) -> dict:
    return accounts.sess_get(_key(uid)) or {}


def _save(uid, f: dict) -> None:
    accounts.sess_set(_key(uid), {k: v for k, v in f.items() if v not in (None, "")})


def _query(f: dict, offset: int = 0):
    return book.search(meal=f.get("meal"), tag=f.get("tag"), q=f.get("q"), max_min=f.get("max_min"), max_kcal=f.get("max_kcal"),
                       appliance=f.get("appliance"), servings=f.get("servings"), main=f.get("main"), limit=PAGE, offset=offset)


def _label(field: str, value) -> str:
    for text, v in FIELDS[field][1]:
        if v == value:
            return text
    return "любой"


def summary(f: dict) -> str:
    parts = []
    for field, (title, _) in FIELDS.items():
        v = f.get(STATE_KEY[field])
        if v is not None:
            parts.append(f"{title.split(' ', 1)[1].lower()}: {_label(field, v).lower()}")
    if f.get("tag"):
        parts.append(f.get("tag"))
    if f.get("q"):
        parts.append(f"«{f['q']}»")
    return " · ".join(parts) if parts else "без фильтров"


def home_view(uid) -> tuple[str, InlineKeyboardMarkup]:
    counts = book.counts_by_meal()
    n, today = book.total(), book.added_today()
    text = (f"📖 Книга рецептов\n\nВ книге {n} рецептов" + (f", сегодня добавилось {today}" if today else "") + ". Каждый новый рецепт, который составляет бот, "
            "попадает в книгу, поэтому она пополняется каждый день. Листать книгу можно без ограничений.\n\n"
            "Нужен рецепт именно под вас: нажмите «Создать» в меню.")
    b = InlineKeyboardBuilder()
    for m in book.MEALS:
        b.button(text=f"{book.MEAL_TITLES[m]} ({counts.get(m, 0)})", callback_data=f"bk:m:{m}")
    b.button(text="📚 Все рецепты", callback_data="bk:m:all")
    b.adjust(2)
    b.row(InlineKeyboardButton(text="⚙️ Подобрать по условиям", callback_data="bk:fl"), InlineKeyboardButton(text="🏷 Хештеги", callback_data="bk:tg"))
    b.row(InlineKeyboardButton(text="🔎 Найти по слову", callback_data="bk:q"),
          InlineKeyboardButton(text=f"⭐ Моё избранное ({len(favorites.get_favorites(uid))})", callback_data="bk:fav:0"))
    b.row(InlineKeyboardButton(text="⬅️ Меню", callback_data="menu:open"))
    return text, b.as_markup()


def list_view(uid, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    f = _flt(uid)
    page = max(page, 0)
    rows, total = _query(f, page * PAGE)
    pages = max(1, -(-total // PAGE))
    if page >= pages and total:
        page, (rows, total) = pages - 1, _query(f, (pages - 1) * PAGE)
    lines = [f"📖 Книга рецептов: {summary(f)}", f"Найдено: {total}" + (f" (страница {page + 1} из {pages})" if pages > 1 else "")]
    b = InlineKeyboardBuilder()
    for r in rows:
        extra = " · ".join(x for x in (f"{r['kcal']} ккал" if r["kcal"] else "", f"{r['minutes']} мин" if r["minutes"] else "") if x)
        title = r["title"] if len(r["title"]) <= 34 else r["title"][:32] + "…"
        b.row(InlineKeyboardButton(text=f"🍽 {title}" + (f" · {extra}" if extra else ""), callback_data=f"bk:v:{r['id']}"))
    if not rows:
        lines += ["", "Под такие условия в книге пока ничего нет. Ослабьте фильтры или нажмите «Создать» в меню: бот составит новый рецепт, и он попадёт в книгу."]
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"bk:l:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="bk:noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"bk:l:{page + 1}"))
        b.row(*nav)
    b.row(InlineKeyboardButton(text="⚙️ Условия", callback_data="bk:fl"), InlineKeyboardButton(text="🔎 Слово", callback_data="bk:q"),
          InlineKeyboardButton(text="🏷 Теги", callback_data="bk:tg"))
    b.row(InlineKeyboardButton(text="⬅️ Книга", callback_data="bk:h"))
    return "\n".join(lines), b.as_markup()


def filters_view(uid) -> tuple[str, InlineKeyboardMarkup]:
    f = _flt(uid)
    _, total = _query(f, 0)
    b = InlineKeyboardBuilder()
    for field, (title, _) in FIELDS.items():
        v = f.get(STATE_KEY[field])
        b.button(text=f"{title}: {_label(field, v)}", callback_data=f"bk:f:{field}")
    b.adjust(2)
    b.row(InlineKeyboardButton(text=f"✅ Показать ({total})", callback_data="bk:l:0"), InlineKeyboardButton(text="🧹 Сбросить", callback_data="bk:x"))
    b.row(InlineKeyboardButton(text="⬅️ Книга", callback_data="bk:h"))
    return ("⚙️ Подобрать по условиям\n\nВыберите, на чём готовить, на сколько человек, основной ингредиент, калорийность порции и время. "
            f"Подходит рецептов: {total}."), b.as_markup()


async def show_home(event, mode: str = "auto") -> None:
    uid = (event.message if isinstance(event, CallbackQuery) else event).chat.id
    text, markup = home_view(uid)
    await ui.show(event, text, markup, mode=mode)


@router.callback_query(F.data == "book:open")
async def open_book(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await show_home(callback, "auto")


@router.callback_query(F.data == "bk:h")
async def home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await show_home(callback, "edit")


@router.callback_query(F.data == "bk:noop")
async def noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("bk:m:"))
async def pick_meal(callback: CallbackQuery):
    meal = callback.data.split(":")[2]
    uid = callback.message.chat.id
    _save(uid, {"meal": meal} if meal in book.MEALS else {})
    await callback.answer()
    text, markup = list_view(uid, 0)
    await ui.show(callback, text, markup, mode="edit")


@router.callback_query(F.data.startswith("bk:l:"))
async def list_page(callback: CallbackQuery):
    arg = callback.data.split(":")[2]
    await callback.answer()
    text, markup = list_view(callback.message.chat.id, int(arg) if arg.isdigit() else 0)
    await ui.show(callback, text, markup, mode="edit")


@router.callback_query(F.data == "bk:fl")
async def filters(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    text, markup = filters_view(callback.message.chat.id)
    await ui.show(callback, text, markup, mode="edit")


@router.callback_query(F.data.startswith("bk:f:"))
async def open_picker(callback: CallbackQuery):
    field = callback.data.split(":")[2]
    if field not in FIELDS:
        await callback.answer()
        return
    await callback.answer()
    title, options = FIELDS[field]
    cur = _flt(callback.message.chat.id).get(STATE_KEY[field])
    b = InlineKeyboardBuilder()
    for i, (text, v) in enumerate(options):
        b.button(text=("✅ " if v == cur else "") + text, callback_data=f"bk:o:{field}:{i}")
    b.adjust(2)
    b.row(InlineKeyboardButton(text="⬅️ Условия", callback_data="bk:fl"))
    await ui.show(callback, f"{title}: что выбрать?", b.as_markup(), mode="edit")


@router.callback_query(F.data.startswith("bk:o:"))
async def choose_option(callback: CallbackQuery):
    try:
        _, _, field, idx = callback.data.split(":")
        value = FIELDS[field][1][int(idx)][1]
    except (ValueError, KeyError, IndexError):
        await callback.answer()
        return
    uid = callback.message.chat.id
    f = _flt(uid)
    f[STATE_KEY[field]] = value
    _save(uid, f)
    await callback.answer()
    text, markup = filters_view(uid)
    await ui.show(callback, text, markup, mode="edit")


@router.callback_query(F.data == "bk:x")
async def reset(callback: CallbackQuery):
    _save(callback.message.chat.id, {})
    await callback.answer("Условия сброшены")
    text, markup = filters_view(callback.message.chat.id)
    await ui.show(callback, text, markup, mode="edit")


@router.callback_query(F.data == "bk:tg")
async def tags(callback: CallbackQuery):
    await callback.answer()
    top = book.top_tags(16)
    b = InlineKeyboardBuilder()
    for i, (tag, n) in enumerate(top):
        b.button(text=f"{tag} ({n})", callback_data=f"bk:t:{i}")
    b.adjust(2)
    b.row(InlineKeyboardButton(text="⬅️ Книга", callback_data="bk:h"))
    await ui.show(callback, "🏷 Хештеги: выберите, чтобы показать рецепты с этой пометкой." if top else "Хештегов пока нет: в книге нет рецептов.",
                  b.as_markup(), mode="edit")


@router.callback_query(F.data.startswith("bk:t:"))
async def pick_tag(callback: CallbackQuery):
    top = book.top_tags(16)
    try:
        tag = top[int(callback.data.split(":")[2])][0]
    except (ValueError, IndexError):
        await callback.answer()
        return
    uid = callback.message.chat.id
    f = _flt(uid)
    f["tag"] = tag
    _save(uid, f)
    await callback.answer()
    text, markup = list_view(uid, 0)
    await ui.show(callback, text, markup, mode="edit")


@router.callback_query(F.data == "bk:q")
async def ask_word(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BookForm.q)
    await callback.answer()
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Отмена", callback_data="bk:h")
    await ui.show(callback, "🔎 Напишите слово: блюдо или продукт, например «борщ», «говядина», «творог». "
                  "Чтобы убрать прежнее слово, напишите «-».", b.as_markup(), mode="edit")


@router.message(BookForm.q, F.text)
async def got_word(message: Message, state: FSMContext):
    uid = message.chat.id
    await ui.drop(message)
    await state.clear()
    f = _flt(uid)
    word = (message.text or "").strip()[:40]
    f["q"] = None if word in ("-", "—", "") else word
    _save(uid, f)
    text, markup = list_view(uid, 0)
    await ui.show(message, text, markup, mode="tracked")


@router.callback_query(F.data.startswith("bk:v:"))
async def view_recipe(callback: CallbackQuery):
    """Рецепт из книги: карточка-картинка и текст с обычными кнопками (готовить, список покупок, PDF). Отмечаем показ, чтобы бот не предлагал его снова."""
    uid = callback.message.chat.id
    r = book.get(callback.data.split(":")[2])
    if not r:
        await callback.answer("Рецепт не найден", show_alert=True)
        return
    await callback.answer()
    book.mark_seen(uid, r["id"])
    set_last_recipe(uid, r["title"], r["text"], r["cuisine"])
    usage.event("book_view", uid)
    try:
        await callback.message.answer_photo(BufferedInputFile(cards.make_card(r["title"], r["text"]), "recipe.png"))
    except Exception:  # noqa: BLE001 — картинка не обязательна
        logger.exception("Карточка рецепта не отправилась")
    markup = recipe_kb(r["text"], uid) if recipe_kb else None
    if markup:
        markup.inline_keyboard.append([InlineKeyboardButton(text="📖 К книге", callback_data="bk:l:0")])
    await callback.message.answer(r["text"] + "\n\n" + " ".join(r["tags"]), reply_markup=markup)


@router.callback_query(F.data.startswith("bk:fav:"))
async def my_favorites(callback: CallbackQuery):
    arg = callback.data.split(":")[2]
    await callback.answer()
    await show_favorites(callback, int(arg) if arg.isdigit() else 0, "edit")


async def show_favorites(event, page: int = 0, mode: str = "auto") -> None:
    """«⭐ Избранное»: свои сохранённые рецепты, новые сверху."""
    uid = (event.message if isinstance(event, CallbackQuery) else event).chat.id
    favs = list(reversed(favorites.get_favorites(uid)))
    pages = max(1, -(-len(favs) // PAGE))
    page = max(0, min(page, pages - 1))
    b = InlineKeyboardBuilder()
    for fav in favs[page * PAGE:(page + 1) * PAGE]:
        title = fav["title"] if len(fav["title"]) <= 40 else fav["title"][:38] + "…"
        b.row(InlineKeyboardButton(text=f"🍽 {title}", callback_data=f"fav:view:{fav['id']}"))
    if pages > 1:
        nav = ([InlineKeyboardButton(text="◀️", callback_data=f"bk:fav:{page - 1}")] if page > 0 else [])
        nav += [InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="bk:noop")]
        nav += ([InlineKeyboardButton(text="▶️", callback_data=f"bk:fav:{page + 1}")] if page < pages - 1 else [])
        b.row(*nav)
    b.row(InlineKeyboardButton(text="📖 Книга рецептов", callback_data="bk:h"), InlineKeyboardButton(text="⬅️ Меню", callback_data="menu:open"))
    if favs:
        text = f"⭐ Моё избранное ({len(favs)})\n\nСохранить рецепт можно кнопкой «⭐ В избранное» под ним."
    else:
        text = "⭐ В избранном пока пусто. Сохраните рецепт кнопкой «⭐ В избранное» под ним."
    await ui.show(event, text, b.as_markup(), mode=mode)
