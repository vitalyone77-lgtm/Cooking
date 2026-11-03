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
import charts
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


def diary_view(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    day = accounts.diary_day(uid)
    en = accounts.energy_get(uid)
    target = en["tdee"] if en else None
    lines = ["📒 Дневник питания, сегодня", ""]
    lines.append(f"Съедено: {_fmt(day['kcal'])} ккал" + (f" из ≈{_fmt(target)}" if target else ""))
    if target:
        lines.append(_bar(day["kcal"], target) + ("  (больше расхода)" if day["kcal"] > target else ""))
    else:
        lines.append("Расход не задан: нажмите «⚙️ Расход», чтобы посчитать примерно.")
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
    b.row(InlineKeyboardButton(text="🍽 Съел рецепт", callback_data="dy:eat"))
    b.row(InlineKeyboardButton(text="📊 График 7 дн.", callback_data="dy:ch:7"), InlineKeyboardButton(text="📊 14 дн.", callback_data="dy:ch:14"),
          InlineKeyboardButton(text="30 дн.", callback_data="dy:ch:30"))
    b.row(InlineKeyboardButton(text="⚙️ Расход", callback_data="dy:calc"), InlineKeyboardButton(text="⬅️ Профиль", callback_data="pf:back"))
    dels = [InlineKeyboardButton(text=f"❌ {i}", callback_data=f"dy:del:{it['id']}") for i, it in enumerate(day["items"][:12], 1)]
    for k in range(0, len(dels), 6):
        b.row(*dels[k:k + 6])
    return "\n".join(lines), b.as_markup()


def _open_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📒 Открыть дневник", callback_data="dy:open")
    return b.as_markup()


async def _need_diary(callback: CallbackQuery) -> bool:
    if plans.diary_on(callback.message.chat.id):
        return True
    await callback.answer("Включите «Дневник питания» в профиле: ⚙️ Функции", show_alert=True)
    return False


async def _show(callback: CallbackQuery, edit: bool = True) -> None:
    text, markup = diary_view(callback.message.chat.id)
    if edit:
        try:
            await callback.message.edit_text(text, reply_markup=markup, parse_mode=None)
            return
        except Exception:  # noqa: BLE001 — сообщение старое или не наше
            pass
    await callback.message.answer(text, reply_markup=markup, parse_mode=None)


@router.callback_query(F.data == "dy:open")
async def open_diary(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await _need_diary(callback):
        return
    await callback.answer()
    await _show(callback, edit=False)


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
    await callback.answer("Записал")
    await callback.message.answer(f"✅ Записал в дневник одну порцию: {d['title']}, {d['kcal']} ккал.\n{SHORT_NOTE}",
                                  reply_markup=_open_kb(), parse_mode=None)


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
    for i, name in enumerate(foods.CATEGORIES):
        b.button(text=name, callback_data=f"dy:c:{i}")
    b.adjust(2)
    b.row(InlineKeyboardButton(text="🔎 Найти по названию", callback_data="dy:find"))
    b.row(InlineKeyboardButton(text="⬅️ Дневник", callback_data="dy:open"))
    await callback.message.edit_text("➕ Что вы съели? Выберите раздел.\n\nЗначения справочные, средние.", reply_markup=b.as_markup(), parse_mode=None)


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
    await callback.message.edit_text(f"{title}\n\nВ кнопке: ккал в обычной порции.", reply_markup=b.as_markup(), parse_mode=None)


def _portion_view(food: dict) -> tuple[str, InlineKeyboardMarkup]:
    n = foods.nutrition(food, food["grams"])
    text = (f"{food['name']}\n{food['label']} ≈ {n['kcal']} ккал (Б {n['protein']:g} · Ж {n['fat']:g} · У {n['carb']:g})\n"
            f"На 100 г: {food['kcal100']} ккал.\n\nСколько съели?")
    b = InlineKeyboardBuilder()
    for label, m in (("½ порции", 5), ("1 порция", 10), ("1½", 15), ("2 порции", 20)):
        b.button(text=label, callback_data=f"dy:p:{food['id']}:{m}")
    b.adjust(2, 2)
    b.row(InlineKeyboardButton(text="✏️ Свои граммы", callback_data=f"dy:g:{food['id']}"))
    b.row(InlineKeyboardButton(text="⬅️ Разделы", callback_data="dy:cat"))
    return text, b.as_markup()


@router.callback_query(F.data.startswith("dy:f:"))
async def choose_portion(callback: CallbackQuery):
    food = foods.BY_ID.get(callback.data.split(":", 2)[2])
    if not food or not await _need_diary(callback):
        await callback.answer()
        return
    await callback.answer()
    text, markup = _portion_view(food)
    await callback.message.edit_text(text, reply_markup=markup, parse_mode=None)


def _add_food(uid: int, food: dict, grams: float) -> dict:
    n = foods.nutrition(food, grams)
    accounts.diary_add(uid, f"{food['name']} ({int(round(grams))} г)", n["kcal"], n["protein"], n["fat"], n["carb"], "list")
    usage.event("diary_add", uid)
    return n


@router.callback_query(F.data.startswith("dy:p:"))
async def add_portion(callback: CallbackQuery):
    try:
        _, _, fid, mult = callback.data.split(":")
        food, m = foods.BY_ID[fid], int(mult)
    except (ValueError, KeyError):
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
    await _show(callback)


@router.callback_query(F.data.startswith("dy:g:"))
async def ask_grams(callback: CallbackQuery, state: FSMContext):
    food = foods.BY_ID.get(callback.data.split(":", 2)[2])
    if not food or not await _need_diary(callback):
        await callback.answer()
        return
    await state.set_state(DiaryForm.grams)
    await state.update_data(dy_food=food["id"])
    await callback.answer()
    await callback.message.edit_text(f"{food['name']}: сколько граммов съели? Напишите число, например 180.", parse_mode=None)


@router.message(DiaryForm.grams, F.text)
async def got_grams(message: Message, state: FSMContext):
    uid = message.chat.id
    food = foods.BY_ID.get((await state.get_data()).get("dy_food"))
    m = re.search(r"\d+[.,]?\d*", message.text or "")
    grams = float(m.group(0).replace(",", ".")) if m else 0
    if not food or not 5 <= grams <= 2000:
        await message.answer("Напишите вес в граммах от 5 до 2000, например 180.", parse_mode=None)
        return
    await state.clear()
    try:
        n = _add_food(uid, food, grams)
    except ValueError as e:
        await message.answer(str(e), parse_mode=None)
        return
    text, markup = diary_view(uid)
    await message.answer(f"✅ Записал: {n['kcal']} ккал.\n\n{text}", reply_markup=markup, parse_mode=None)


# ---------- поиск и своё блюдо ----------

@router.callback_query(F.data == "dy:find")
async def ask_find(callback: CallbackQuery, state: FSMContext):
    if not await _need_diary(callback):
        return
    await state.set_state(DiaryForm.find)
    await callback.answer()
    await callback.message.edit_text("🔎 Напишите название, например «борщ» или «куриная грудка».\n\n"
                                     "Своё блюдо можно записать сразу с калориями: «пицца 450».", parse_mode=None)


def parse_manual(text: str) -> tuple[str, int] | None:
    m = re.fullmatch(r"\s*(.+?)\s+(\d{2,4})\s*(?:ккал|кал|kcal)?\s*", text or "", re.I)
    return (m.group(1), int(m.group(2))) if m else None


@router.message(DiaryForm.find, F.text)
async def got_find(message: Message, state: FSMContext):
    uid = message.chat.id
    manual = parse_manual(message.text)
    if manual:
        await state.clear()
        try:
            accounts.diary_add(uid, manual[0], manual[1], src="manual")
        except ValueError as e:
            await message.answer(str(e), parse_mode=None)
            return
        usage.event("diary_add", uid)
        text, markup = diary_view(uid)
        await message.answer(f"✅ Записал: {manual[0]}, {manual[1]} ккал.\n\n{text}", reply_markup=markup, parse_mode=None)
        return
    found = foods.search(message.text)
    if not found:
        await message.answer("Ничего не нашёл. Попробуйте другое слово или запишите сразу с калориями: «пицца 450».", parse_mode=None)
        return
    await state.clear()
    b = InlineKeyboardBuilder()
    for f in found:
        b.button(text=f"{f['name']} · {foods.portion_kcal(f)}", callback_data=f"dy:f:{f['id']}")
    b.adjust(1)
    b.row(InlineKeyboardButton(text="⬅️ Дневник", callback_data="dy:open"))
    await message.answer("Нашёл. В кнопке: ккал в обычной порции.", reply_markup=b.as_markup(), parse_mode=None)


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
async def chart(callback: CallbackQuery):
    uid = callback.message.chat.id
    if not accounts.consent_get(uid):
        await callback.answer("Сначала нажмите «Согласен» в профиле", show_alert=True)
        return
    try:
        days = max(2, min(int(callback.data.split(":")[2]), 60))
    except ValueError:
        days = 14
    weights = accounts.weights_list(uid, 120)
    goal = accounts.goals_get(uid)["weight_goal"]
    if plans.diary_on(uid):
        series = accounts.diary_series(uid, days)
        en = accounts.energy_get(uid)
        st = accounts.diary_stats(series)
        png = charts.diary_chart(series, en["tdee"] if en else None, weights)
        cap = (f"В среднем {_fmt(st['avg_kcal'])} ккал в день (по {st['logged']} дн. с записями)" if st["logged"] else "Записей за этот период нет")
        if en:
            cap += f". Расход примерно {_fmt(en['tdee'])} ккал"
        if st["logged"]:
            cap += f"\nБ {st['avg_protein']:g} · Ж {st['avg_fat']:g} · У {st['avg_carb']:g} г в день"
        cap += f"\n{SHORT_NOTE}"
    elif weights:
        png = charts.weight_chart(weights, goal)
        cap = "График веса. Оценка динамики, не медицинское заключение."
    else:
        await callback.answer("Пока нечего показывать: запишите вес или включите дневник", show_alert=True)
        return
    await callback.answer()
    usage.event("diary_chart", uid)
    await callback.message.answer_photo(BufferedInputFile(png, "chart.png"), caption=cap[:1000])


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
    b.adjust(2)
    await callback.message.edit_text(
        "⚙️ Примерный расход калорий в день.\n\nСчитаем по формуле Миффлина — Сан Жеора. Пол, возраст и рост мы не сохраняем: "
        "останется только итоговое число и уровень активности.\n\nВаш пол?", reply_markup=b.as_markup(), parse_mode=None)


@router.callback_query(F.data.startswith("dy:s:"))
async def calc_sex(callback: CallbackQuery, state: FSMContext):
    sex = callback.data.split(":")[2]
    if sex not in ("m", "f"):
        await callback.answer()
        return
    await state.set_state(DiaryForm.age)
    await state.update_data(dy_sex=sex)
    await callback.answer()
    await callback.message.edit_text("Сколько вам лет? Напишите число.", parse_mode=None)


async def _number(message: Message, lo: float, hi: float, hint: str) -> float | None:
    m = re.search(r"\d+[.,]?\d*", message.text or "")
    v = float(m.group(0).replace(",", ".")) if m else None
    if v is None or not lo <= v <= hi:
        await message.answer(hint, parse_mode=None)
        return None
    return v


@router.message(DiaryForm.age, F.text)
async def calc_age(message: Message, state: FSMContext):
    v = await _number(message, 14, 90, "Возраст от 14 до 90, напишите число.")
    if v is None:
        return
    await state.update_data(dy_age=v)
    await state.set_state(DiaryForm.height)
    await message.answer("Какой у вас рост в сантиметрах? Например 172.", parse_mode=None)


@router.message(DiaryForm.height, F.text)
async def calc_height(message: Message, state: FSMContext):
    v = await _number(message, 120, 230, "Рост от 120 до 230 см, напишите число.")
    if v is None:
        return
    await state.update_data(dy_height=v)
    await state.set_state(DiaryForm.weight)
    ws = accounts.weights_list(message.chat.id, 1)
    tip = f" Последний записанный: {ws[-1]['kg']:g} кг." if ws else ""
    await message.answer(f"Какой у вас вес в килограммах?{tip}", parse_mode=None)


@router.message(DiaryForm.weight, F.text)
async def calc_weight(message: Message, state: FSMContext):
    v = await _number(message, 30, 250, "Вес от 30 до 250 кг, напишите число.")
    if v is None:
        return
    await state.update_data(dy_weight=v)
    await state.set_state(DiaryForm.activity)
    await message.answer("Какая у вас повседневная активность?", reply_markup=_activity_kb(), parse_mode=None)


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
    accounts.energy_set(callback.message.chat.id, key, tdee)
    usage.event("diary_calc", callback.message.chat.id)
    await callback.answer()
    await callback.message.edit_text(
        f"Примерный расход: {_fmt(tdee)} ккал в день.\nАктивность: {accounts.ACTIVITY[key][1].lower()}.\n\n"
        f"Это ориентир: реальный расход может отличаться на 10–20%. Ориентиры по питанию: около {_fmt(tdee)} ккал, чтобы держать вес; "
        f"на {_fmt(int(tdee * 0.85))}–{_fmt(int(tdee * 0.9))} ккал вес обычно снижается медленно. Сохранены только это число и уровень активности.\n\n"
        f"{accounts.DIARY_NOTE}", reply_markup=_open_kb(), parse_mode=None)
