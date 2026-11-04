"""
Дневник питания в боте (включается галочкой в профиле, нужно согласие): записать съеденное из рецепта, из справочника
или вручную, увидеть итог дня, график «калории столбиками и вес линией» картинкой, посчитать примерный расход.
Калории оценочные: оговорка показывается на экранах дневника.
"""
import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import accounts
import cards
import ui
import charts
import food_ai
import foods
import plans
import usage
from states import DiaryForm

logger = logging.getLogger(__name__)
router = Router(name="diary")

SHORT_NOTE = "Калории оценочные. Не врач и не диетолог."


def _fmt(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def _bar(value: int, target: int | None) -> str:
    if not target:
        return ""
    pct = value / target
    full = min(10, int(round(pct * 10)))
    return "▓" * full + "░" * (10 - full) + f" {int(round(pct * 100))}%"


def summary_line(uid: int) -> str:
    """Строка для верха профиля: «📒 Сегодня: 1 240 из ≈2 150 ккал»."""
    day = accounts.diary_day(uid)
    en = accounts.energy_get(uid)
    return f"📒 Сегодня: {_fmt(day['kcal'])}" + (f" из ≈{_fmt(en['tdee'])}" if en else "") + " ккал"


def diary_view(uid: int, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    day = accounts.diary_day(uid)
    en = accounts.energy_get(uid)
    target = en["tdee"] if en else None
    lines = ([note, ""] if note else []) + ["📒 Дневник питания, сегодня", ""]
    lines.append(f"Съедено: {_fmt(day['kcal'])} ккал" + (f" из ≈{_fmt(target)}" if target else ""))
    if target:
        lines.append(_bar(day["kcal"], target) + ("  (больше расхода)" if day["kcal"] > target else ""))
    else:
        lines.append("Расход не задан: нажмите «🧮 Расход», чтобы посчитать примерно.")
    lines.append(f"Б {day['protein']:g} г · Ж {day['fat']:g} г · У {day['carb']:g} г")
    lines.append("")
    if day["items"]:
        for i, it in enumerate(day["items"][:12], 1):
            lines.append(f"{i}. {it['title']} — {it['kcal']} ккал")
    else:
        lines.append("Пока пусто. Добавьте приём пищи кнопками ниже.")
    lines += ["", SHORT_NOTE]
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Из списка", callback_data="dy:cat"), InlineKeyboardButton(text="🔎 Найти", callback_data="dy:find"))
    b.row(InlineKeyboardButton(text="🍽 Съел рецепт", callback_data="dy:eat"),
          *([] if en else [InlineKeyboardButton(text="🧮 Расход", callback_data="dy:calc")]))
    b.row(InlineKeyboardButton(text="⬅️ Профиль", callback_data="pf:main"))
    dels = [InlineKeyboardButton(text=f"❌ {i}", callback_data=f"dy:del:{it['id']}") for i, it in enumerate(day["items"][:12], 1)]
    for k in range(0, len(dels), 6):
        b.row(*dels[k:k + 6])
    return "\n".join(lines), b.as_markup()


def _open_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📒 Открыть дневник", callback_data="dy:open")
    b.button(text="⬅️ Профиль", callback_data="pf:main")
    b.adjust(1)
    return b.as_markup()


def _back_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Профиль", callback_data="pf:back")
    return b.as_markup()


def _cancel_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Отмена", callback_data="dy:open")
    return b.as_markup()


async def _need_diary(callback: CallbackQuery) -> bool:
    if plans.diary_on(callback.message.chat.id):
        return True
    await callback.answer("Включите «Дневник питания» в профиле: ⚙️ Функции", show_alert=True)
    return False


async def _show(callback: CallbackQuery, note: str = "") -> None:
    text, markup = diary_view(callback.message.chat.id, note)
    await ui.show(callback, text, markup, mode="edit")


@router.callback_query(F.data == "dy:open")
async def open_diary(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await _need_diary(callback):
        return
    await callback.answer()
    await _show(callback)


@router.callback_query(F.data == "dy:eat")
async def eat_recipe(callback: CallbackQuery):
    """Одна порция последнего показанного рецепта (КБЖУ берётся из самого рецепта)."""
    from last_recipe import get_last_recipe
    uid = callback.message.chat.id
    if not await _need_diary(callback):
        return
    from shopping import extract_dish_title
    shown = callback.message.text or ""
    if "Калорийность" in shown:                      # нажали под самим рецептом: берём именно его
        d = cards.parse(extract_dish_title(shown), shown)
    else:                                            # нажали в дневнике: последний показанный рецепт
        last = get_last_recipe(uid)
        d = cards.parse(last["title"], last["text"]) if last else None
    if not d or not d["kcal"]:
        await callback.answer("В рецепте нет калорийности. Выберите блюдо из списка.", show_alert=True)
        return
    try:
        accounts.diary_add(uid, d["title"], int(float(d["kcal"].replace(",", "."))), _f(d["protein"]), _f(d["fat"]), _f(d["carb"]), "recipe")
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
        return
    usage.event("diary_add", uid)
    await callback.answer(f"✅ В дневник: {d['title'][:60]}, {d['kcal']} ккал. Оценка примерная.", show_alert=True)


def _f(v) -> float:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


# ---------- выбор из списка ----------

@router.callback_query(F.data == "dy:cat")
async def categories(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await _need_diary(callback):
        return
    await callback.answer()
    b = InlineKeyboardBuilder()
    by_name = {f["name"]: f for f in foods.FOODS}
    freq = [by_name[n] for n in accounts.diary_frequent(callback.message.chat.id) if n in by_name]
    for f in freq:
        b.button(text=f"⭐ {f['name']} · {foods.portion_kcal(f)}", callback_data=f"dy:f:{f['id']}")
    b.adjust(1)
    names = InlineKeyboardBuilder()
    for i, name in enumerate(foods.CATEGORIES):
        names.button(text=name, callback_data=f"dy:c:{i}")
    names.adjust(2)
    b.attach(names)
    b.row(InlineKeyboardButton(text="🔎 Найти", callback_data="dy:find"), InlineKeyboardButton(text="⬅️ Дневник", callback_data="dy:open"))
    await ui.show(callback, "➕ Что вы съели? " + ("Часто записываете: ⭐. " if freq else "") + "Выберите раздел.\n\nЗначения справочные, средние.",
                  b.as_markup(), mode="edit")


@router.callback_query(F.data.startswith("dy:c:"))
async def category_items(callback: CallbackQuery):
    if not await _need_diary(callback):
        return
    try:
        cat = int(callback.data.split(":")[2])
        title = foods.CATEGORIES[cat]
    except (ValueError, IndexError):
        await callback.answer()
        return
    await callback.answer()
    b = InlineKeyboardBuilder()
    for f in foods.in_category(cat):
        b.button(text=f"{f['name']} · {foods.portion_kcal(f)}", callback_data=f"dy:f:{f['id']}")
    b.adjust(1)
    b.row(InlineKeyboardButton(text="⬅️ Разделы", callback_data="dy:cat"))
    await ui.show(callback, f"{title}\n\nВ кнопке: ккал в обычной порции.", b.as_markup(), mode="edit")


PORTIONS = (("Малая", 7), ("Стандарт", 10), ("Большая", 15))      # доли обычной порции в десятых


def _portion_view(food: dict) -> tuple[str, InlineKeyboardMarkup]:
    g = food["grams"]
    lines = [food["name"], f"Обычная порция: {food['label']}. На 100 г: {int(food['kcal100'])} ккал.", ""]
    if food.get("ai"):
        lines[1:1] = ["🤖 Оценка ИИ, может ошибаться."]
    for label, m in PORTIONS:
        gr = int(round(g * m / 10))
        n = foods.nutrition(food, gr)
        lines.append(f"{label}: {gr} г ≈ {n['kcal']} ккал (Б {n['protein']:g} · Ж {n['fat']:g} · У {n['carb']:g})")
    lines += ["", "Сколько съели?"]
    b = InlineKeyboardBuilder()
    b.row(*[InlineKeyboardButton(text=label, callback_data=f"dy:p:{food['id']}:{m}") for label, m in PORTIONS],
          InlineKeyboardButton(text="✏️ Граммы", callback_data=f"dy:g:{food['id']}"))
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="dy:cat"))
    return "\n".join(lines), b.as_markup()


@router.callback_query(F.data.startswith("dy:f:"))
async def choose_portion(callback: CallbackQuery):
    food = food_ai.lookup(callback.data.split(":", 2)[2])
    if not food or not await _need_diary(callback):
        await callback.answer()
        return
    await callback.answer()
    text, markup = _portion_view(food)
    await ui.show(callback, text, markup, mode="edit")


def _add_food(uid: int, food: dict, grams: float) -> dict:
    n = foods.nutrition(food, grams)
    accounts.diary_add(uid, f"{food['name']} ({int(round(grams))} г)", n["kcal"], n["protein"], n["fat"], n["carb"], "ai" if food.get("ai") else "list")
    usage.event("diary_add", uid)
    return n


@router.callback_query(F.data.startswith("dy:p:"))
async def add_portion(callback: CallbackQuery):
    try:
        _, _, fid, mult = callback.data.split(":")
        food, m = food_ai.lookup(fid), int(mult)
        if not food or m not in (5, 7, 10, 15, 20):
            raise ValueError(m)
    except ValueError:
        await callback.answer()
        return
    if not await _need_diary(callback):
        return
    try:
        n = _add_food(callback.message.chat.id, food, food["grams"] * m / 10)
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
        return
    await callback.answer(f"Записал: {n['kcal']} ккал")
    await _show(callback, f"✅ Записал: {n['kcal']} ккал")


@router.callback_query(F.data.startswith("dy:g:"))
async def ask_grams(callback: CallbackQuery, state: FSMContext):
    food = food_ai.lookup(callback.data.split(":", 2)[2])
    if not food or not await _need_diary(callback):
        await callback.answer()
        return
    await state.set_state(DiaryForm.grams)
    await state.update_data(dy_food=food["id"])
    await callback.answer()
    await ui.show(callback, f"{food['name']}: сколько граммов съели? Напишите число, например 180.",
                  _cancel_kb(), mode="edit")


@router.message(DiaryForm.grams, F.text)
async def got_grams(message: Message, state: FSMContext):
    uid = message.chat.id
    await ui.drop(message)
    food = food_ai.lookup((await state.get_data()).get("dy_food"))
    m = re.search(r"\d+[.,]?\d*", message.text or "")
    grams = float(m.group(0).replace(",", ".")) if m else 0
    if not food or not 5 <= grams <= 2000:
        await ui.show(message, f"{food['name'] if food else 'Продукт'}: напишите вес в граммах от 5 до 2000, например 180.", _cancel_kb(), mode="tracked")
        return
    await state.clear()
    try:
        n = _add_food(uid, food, grams)
    except ValueError as e:
        await ui.show(message, str(e), _cancel_kb(), mode="tracked")
        return
    text, markup = diary_view(uid, f"✅ Записал: {n['kcal']} ккал")
    await ui.show(message, text, markup, mode="tracked")


# ---------- поиск и своё блюдо ----------

@router.callback_query(F.data == "dy:find")
async def ask_find(callback: CallbackQuery, state: FSMContext):
    if not await _need_diary(callback):
        return
    await state.set_state(DiaryForm.find)
    await callback.answer()
    await ui.show(callback, "🔎 Напишите название, например «борщ» или «куриная грудка». Если в справочнике такого нет, спрошу у ИИ.\n\n"
                  "Своё блюдо можно записать сразу с калориями: «пицца 450».", _cancel_kb(), mode="edit")


def parse_manual(text: str) -> tuple[str, int] | None:
    m = re.fullmatch(r"\s*(.+?)\s+(\d{2,4})\s*(?:ккал|кал|kcal)?\s*", text or "", re.I)
    return (m.group(1), int(m.group(2))) if m else None


@router.message(DiaryForm.find, F.text)
async def got_find(message: Message, state: FSMContext):
    uid = message.chat.id
    await ui.drop(message)
    manual = parse_manual(message.text)
    if manual:
        await state.clear()
        try:
            accounts.diary_add(uid, manual[0], manual[1], src="manual")
        except ValueError as e:
            await ui.show(message, str(e), _cancel_kb(), mode="tracked")
            return
        usage.event("diary_add", uid)
        text, markup = diary_view(uid, f"✅ Записал: {manual[0]}, {manual[1]} ккал")
        await ui.show(message, text, markup, mode="tracked")
        return
    query = (message.text or "").strip()[:60]
    found = foods.search(query)
    if not found:
        await ui.show(message, "В справочнике такого нет, спрашиваю у ИИ…", None, mode="tracked")
        food, why = await food_ai.estimate(uid, query)
        if not food:
            await ui.show(message, why, _cancel_kb(), mode="tracked")     # состояние поиска остаётся: можно написать ещё раз
            return
        await state.clear()
        text, markup = _portion_view(food)
        await ui.show(message, text, markup, mode="tracked")
        return
    await state.update_data(dy_q=query)
    await state.set_state(None)
    b = InlineKeyboardBuilder()
    for f in found:
        b.button(text=f"{f['name']} · {foods.portion_kcal(f)}", callback_data=f"dy:f:{f['id']}")
    b.adjust(1)
    b.row(InlineKeyboardButton(text="🤖 Нет нужного: спросить ИИ", callback_data="dy:ask"))
    b.row(InlineKeyboardButton(text="⬅️ Дневник", callback_data="dy:open"))
    await ui.show(message, "Нашёл. В кнопке: ккал в обычной порции.", b.as_markup(), mode="tracked")


@router.callback_query(F.data == "dy:ask")
async def ask_ai(callback: CallbackQuery, state: FSMContext):
    """Нужного блюда в результатах нет: оценка ИИ по тому же запросу."""
    if not await _need_diary(callback):
        return
    query = (await state.get_data()).get("dy_q")
    if not query:
        await callback.answer("Напишите название ещё раз", show_alert=True)
        return
    await callback.answer("Спрашиваю у ИИ…")
    food, why = await food_ai.estimate(callback.message.chat.id, query)
    if not food:
        await ui.show(callback, why, _cancel_kb(), mode="edit")
        return
    await state.clear()
    text, markup = _portion_view(food)
    await ui.show(callback, text, markup, mode="edit")


@router.callback_query(F.data.startswith("dy:del:"))
async def delete_entry(callback: CallbackQuery):
    if not await _need_diary(callback):
        return
    try:
        ok = accounts.diary_delete(callback.message.chat.id, int(callback.data.split(":")[2]))
    except ValueError:
        ok = False
    await callback.answer("Убрал" if ok else "Уже убрано")
    await _show(callback)


# ---------- график ----------

@router.callback_query(F.data.startswith("dy:ch:"))
async def chart(callback: CallbackQuery, state: FSMContext):
    """Старые кнопки «График»: при дневнике показываем главный экран профиля, без дневника только график веса."""
    uid = callback.message.chat.id
    if not accounts.consent_get(uid):
        await callback.answer("Сначала нажмите «Согласен» в профиле", show_alert=True)
        return
    if plans.diary_on(uid):
        from bot_profile import show_profile
        arg = callback.data.split(":")[2]
        await callback.answer()
        days = int(arg) if arg.isdigit() else 30
        await show_profile(callback, state, edit=True, days=days if days in (7, 30, 90) else 30)
        return
    weights = accounts.weights_list(uid, 120)
    if not weights:
        await callback.answer("Пока нечего показывать: запишите вес или включите дневник", show_alert=True)
        return
    await callback.answer()
    usage.event("diary_chart", uid)
    png = charts.weight_chart(weights, accounts.goals_get(uid)["weight_goal"])
    await ui.show(callback, "График веса. Оценка динамики, не медицинское заключение.", _back_kb(), png, mode="edit")


# ---------- расчёт примерного расхода ----------

def _activity_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key, (_, title) in accounts.ACTIVITY.items():
        b.button(text=title, callback_data=f"dy:a:{key}")
    b.adjust(1)
    return b.as_markup()


@router.callback_query(F.data == "dy:calc")
async def calc_start(callback: CallbackQuery, state: FSMContext):
    if not await _need_diary(callback):
        return
    await state.clear()
    await callback.answer()
    b = InlineKeyboardBuilder()
    b.button(text="Мужской", callback_data="dy:s:m")
    b.button(text="Женский", callback_data="dy:s:f")
    b.button(text="⬅️ Отмена", callback_data="pf:set")
    b.adjust(2, 1)
    await ui.show(callback,
        "🧮 Примерный расход калорий в день.\n\nСчитаем по формуле Миффлина — Сан Жеора. Пол, возраст и рост мы не сохраняем: "
        "останется только итоговое число и уровень активности.\n\nВаш пол?", b.as_markup(), mode="edit")


@router.callback_query(F.data.startswith("dy:s:"))
async def calc_sex(callback: CallbackQuery, state: FSMContext):
    sex = callback.data.split(":")[2]
    if sex not in ("m", "f"):
        await callback.answer()
        return
    await state.set_state(DiaryForm.body)
    await state.update_data(dy_sex=sex)
    accounts.sess_set(f"dycalc:{callback.message.chat.id}", {"step": "body"})      # только отметка шага, без цифр
    await callback.answer()
    await ui.show(callback, "Напишите одним сообщением три числа через пробел: возраст, рост (см), вес (кг).\n\n"
                  "Например: 38 175 82", _calc_cancel_kb(), mode="edit")


def _calc_cancel_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Отмена", callback_data="pf:set")
    return b.as_markup()


BODY_HINT = "Нужно три числа через пробел: возраст, рост в сантиметрах и вес в килограммах. Например: 38 175 82"


def parse_body(text: str) -> tuple[float, float, float] | str:
    """«38 175 82» -> (возраст, рост, вес) или текст подсказки, что не так."""
    nums = re.findall(r"\d+(?:[.,]\d+)?", text or "")
    if len(nums) != 3:
        return BODY_HINT
    age, height, weight = (float(n.replace(",", ".")) for n in nums)
    if not 14 <= age <= 90:
        return "Возраст должен быть от 14 до 90. " + BODY_HINT
    if not 120 <= height <= 230:
        return "Рост должен быть от 120 до 230 см. " + BODY_HINT
    if not 30 <= weight <= 250:
        return "Вес должен быть от 30 до 250 кг. " + BODY_HINT
    return age, height, weight


@router.message(DiaryForm.body, F.text)
async def calc_body(message: Message, state: FSMContext):
    await ui.drop(message)
    got = parse_body(message.text)
    if isinstance(got, str):
        await ui.show(message, got, _calc_cancel_kb(), mode="tracked")
        return
    await state.update_data(dy_age=got[0], dy_height=got[1], dy_weight=got[2])
    await state.set_state(DiaryForm.activity)
    await ui.show(message, "Какая у вас повседневная активность?", _activity_kb(), mode="tracked")


@router.callback_query(F.data.startswith("dy:a:"))
async def calc_activity(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split(":")[2]
    data = await state.get_data()
    if key not in accounts.ACTIVITY or "dy_weight" not in data:
        await callback.answer("Начните расчёт заново", show_alert=True)
        return
    try:
        tdee = accounts.calc_tdee(data["dy_sex"], data["dy_age"], data["dy_height"], data["dy_weight"], key)
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
        return
    await state.clear()
    accounts.sess_set(f"dycalc:{callback.message.chat.id}", {"step": "done"})
    accounts.energy_set(callback.message.chat.id, key, tdee)
    usage.event("diary_calc", callback.message.chat.id)
    await callback.answer()
    await ui.show(callback,
        f"Примерный расход: {_fmt(tdee)} ккал в день.\nАктивность: {accounts.ACTIVITY[key][1].lower()}.\n\n"
        f"Это ориентир: реальный расход может отличаться на 10–20%. Сколько есть, чтобы менять вес, мы не советуем: это вопрос к врачу или диетологу. "
        f"Сохранены только это число и уровень активности.\n\n{accounts.DIARY_NOTE}", _open_kb(), mode="edit")
