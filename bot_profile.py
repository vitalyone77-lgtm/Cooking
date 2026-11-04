"""
Личный кабинет в боте: полка, вход на сайт,
подписка и оплата (Telegram Stars сразу; рубли — когда будет токен платёжного сервиса), промокоды,
ссылки блогеров (/start ref_…), админская выдача подписки (/grant).
"""
import logging
import re
import time

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

import accounts
import payanyway
import ui
import config
import plans
import usage
from states import FeedbackForm, ProfileForm
from storage import reminders_enabled, set_reminders_enabled

logger = logging.getLogger(__name__)
router = Router(name="profile")


# ---------- общее: отказ по лимиту / уведомление о пробном периоде ----------

def upsell_kb(uid) -> InlineKeyboardMarkup | None:
    if not plans.can_upsell(uid):
        return None
    b = InlineKeyboardBuilder()
    if plans.trial_available(uid):
        b.button(text=f"🎁 Попробовать {int(plans.cfg('TRIAL_DAYS') or 0)} дней бесплатно", callback_data="pf:trial")
    b.button(text="💎 Подписка", callback_data="pf:sub")
    b.adjust(1)
    return b.as_markup()


async def spend_gate(callback: CallbackQuery, spent, why: str) -> bool:
    """После usage.bot_try_spend: False — подбор отклонён (сообщение уже показано); True — можно продолжать."""
    if not spent:
        await callback.answer()
        await callback.message.answer(why, parse_mode=None, reply_markup=upsell_kb(callback.message.chat.id))
        return False
    if why:      # включился пробный период
        await callback.message.answer(why, parse_mode=None)
    return True


# ---------- экран профиля ----------

async def _need_memory(callback: CallbackQuery) -> bool:
    if plans.has_feature(callback.message.chat.id, "memory"):
        return True
    await callback.answer("Эта функция — в тарифе «Повар» 💎", show_alert=True)
    return False


async def _need_shelf(callback: CallbackQuery) -> bool:
    uid = callback.message.chat.id
    if not plans.has_feature(uid, "memory"):
        await callback.answer("Эта функция — в тарифе «Повар» 💎", show_alert=True)
        return False
    if not plans.shelf_on(uid):
        await callback.answer("Включите «Полку» в разделе «⚙️ Функции» профиля", show_alert=True)
        return False
    return True


def _profile_view(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    mem = plans.has_feature(uid, "memory")
    shelf_on = plans.shelf_on(uid)
    consent = accounts.consent_get(uid)
    g = accounts.goals_get(uid) if consent else None
    diary_on = bool(consent and "diary" in g["feats"])
    lines = ["⚙️ *Настройки*" if diary_on else "👤 *Профиль*", ""]
    lines.append(plans.status_text(uid))
    if shelf_on:
        shelf = accounts.shelf_list(uid)
        lines.append(f"🏠 На полке: {len(shelf)} " + _plural(len(shelf), "продукт", "продукта", "продуктов"))
    lines.append(f"🔔 Напоминания: {'включены' if reminders_enabled(uid) else 'выключены'}")
    if consent:
        lines.append("")
        lines.append(f"🚫 Не использовать: {g['never'] or 'не задано'}" + ("" if mem else "  🔒 тариф «Повар»"))
        lines.append(f"🎯 Цель на день: {accounts.goal_text(uid) or 'не задана'}")
        ws = accounts.weights_list(uid, 30)
        if ws:
            vals = [w["kg"] for w in ws]
            delta = vals[-1] - vals[0]
            goal = f", цель {g['weight_goal']:g}" if g["weight_goal"] else ""
            lines.append(f"⚖️ Вес: {vals[-1]:g} кг{goal}; за период {delta:+.1f} кг (график картинкой: кнопка «📊 График»)")
        else:
            lines.append("⚖️ Вес: не записан")
    else:
        lines += ["", "ℹ️ " + accounts.CONSENT_TEXT]
    lines += ["", "_Аллергии и продукты, которые не нужны на один раз, пиши в анкете в строке «Исключить» — я предложу их кнопкой «Как в прошлый раз»._"]
    rows: list[list[tuple[str, str]]] = []
    if consent:
        rows.append([("🚫 Не использовать" if mem else "💎 Не использовать", "pf:never"), ("🎯 Цель КБЖУ", "pf:goal")])
        rows.append([("⚖️ Вес", "pf:weight")] + ([("🧮 Расход", "dy:calc")] if diary_on else [("📊 График", "dy:ch:30")] if accounts.weights_list(uid, 2) else [])
                    + ([("🏠 Моя полка", "pf:shelf")] if shelf_on else []))
        rows.append([("⚙️ Функции", "pf:fts"), ("🗑 Удалить мои данные", "pf:del")])
    else:
        rows.append([("✅ Согласен", "pf:consent")])
    rows.append([("🌐 Открыть сайт", "pf:site")])
    if plans.paywall():
        if plans.trial_available(uid):
            rows.append([("🎁 Попробовать бесплатно", "pf:trial")])
        rows.append([("💎 Подписка", "pf:sub"), ("🎟 Промокод", "pf:promo")])
    devs = accounts.devices_of(uid)
    rows.append([("🔕 Напоминания" if reminders_enabled(uid) else "🔔 Напоминания", "pf:rem")]
                + ([(f"🔓 Выйти на сайте ({devs})", "pf:unall")] if devs else []))
    rows.append([("❓ Справка", "help:open"), ("⬅️ Профиль" if diary_on else "⬅️ Меню", "pf:main" if diary_on else "menu:open")])
    b = InlineKeyboardBuilder()
    for row in rows:
        b.row(*[InlineKeyboardButton(text=t, callback_data=d) for t, d in row])
    return "\n".join(lines), b.as_markup()


PERIODS = (30, 90, 7)       # кнопка «период» листает по кругу; по умолчанию месяц (за неделю вес почти не меняется)


def _next_period(days: int) -> int:
    return PERIODS[(PERIODS.index(days) + 1) % len(PERIODS)] if days in PERIODS else 30


def _profile_main(uid: int, days: int = 30) -> tuple[str, InlineKeyboardMarkup, bytes | None]:
    """Главный экран профиля при включённом дневнике: график калорий и веса картинкой, коротко итоги, три ряда кнопок."""
    import charts
    from bot_diary import summary_line
    days = days if days in PERIODS else 30
    series = accounts.diary_series(uid, days)
    st = accounts.diary_stats(series)
    en = accounts.energy_get(uid)
    weights = accounts.weights_list(uid, days + 5)
    first = series[0]["day"]
    weights = [w for w in weights if w["day"] >= first]
    lines = ["👤 Профиль", summary_line(uid)]
    if st["logged"]:
        lines.append(f"В среднем {st['avg_kcal']:,} ккал в день".replace(",", " ") + f" (дней с записями: {st['logged']} из {days})")
        shares = charts.macro_shares(series)
        if shares:
            lines.append(f"Белки {shares['protein']}%, жиры {shares['fat']}%, углеводы {shares['carb']}% калорий")
    if weights:
        delta = weights[-1]["kg"] - weights[0]["kg"]
        lines.append(f"⚖️ Вес {weights[-1]['kg']:g} кг" + (f", за период {delta:+.1f} кг" if len(weights) > 1 else ""))
    else:
        lines.append("⚖️ Вес пока не записан")
    goal = accounts.goal_text(uid)
    if goal:
        lines.append(f"🎯 Цель на день: {goal}")
    if not en:
        lines.append("Расход не задан: «⚙️ Настройки → Расход».")
    lines.append("Оценка приблизительная, не врач и не диетолог.")
    png = None
    if st["logged"] or len(weights) >= 2:
        png = charts.diary_chart(series, en["tdee"] if en else None, weights)
    else:
        lines.insert(2, "График появится, когда будут записи о еде или вес за разные дни.")
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📒 Записать еду", callback_data="dy:open"), InlineKeyboardButton(text="⚖️ Вес", callback_data="pf:weight"))
    b.row(InlineKeyboardButton(text=f"📅 {days} дн. ▸ {_next_period(days)}", callback_data=f"pf:p:{_next_period(days)}"),
          InlineKeyboardButton(text="⚙️ Настройки", callback_data="pf:set"))
    b.row(InlineKeyboardButton(text="⬅️ Меню", callback_data="menu:open"))
    return "\n".join(lines), b.as_markup(), png


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _diary_enabled(uid: int) -> bool:
    return plans.diary_on(uid)


async def show_profile(event, state: FSMContext | None = None, edit: bool = False, settings: bool = False, days: int = 30,
                       mode: str | None = None):
    """Профиль. Включён дневник: главный экран это график картинкой, а прежние кнопки лежат в «⚙️ Настройки» (settings=True).
    event — Message или CallbackQuery. edit=True: править нажатое сообщение; иначе удалить прошлый экран и прислать новый."""
    import ui
    uid = (event.message if isinstance(event, CallbackQuery) else event).chat.id
    if state:
        await state.clear()
    mode = mode or ("edit" if edit else "fresh")
    if _diary_enabled(uid) and not settings:
        text, markup, png = _profile_main(uid, days)
        await ui.show(event, text, markup, png, mode=mode)
    else:
        text, markup = _profile_view(uid)
        await ui.show(event, text, markup, None, mode=mode, parse_mode="Markdown")


@router.callback_query(F.data == "profile:open")
async def profile_open(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await show_profile(callback, state, mode="auto")        # кнопка бывает и под рецептом: тогда профиль приходит новым сообщением


@router.message(Command("profile"))
async def cmd_profile(message: Message, state: FSMContext):
    await show_profile(message, state)


@router.callback_query(F.data == "pf:back")
async def profile_back(callback: CallbackQuery, state: FSMContext):
    """Назад из подразделов: при дневнике это «Настройки», иначе обычный профиль."""
    await callback.answer()
    await show_profile(callback, state, edit=True, settings=True)


@router.callback_query(F.data == "pf:main")
async def profile_main(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await show_profile(callback, state, edit=True)


@router.callback_query(F.data == "pf:set")
async def profile_settings(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await show_profile(callback, state, edit=True, settings=True)


@router.callback_query(F.data.startswith("pf:p:"))
async def profile_period(callback: CallbackQuery, state: FSMContext):
    arg = callback.data.split(":")[2]
    await callback.answer()
    await show_profile(callback, state, edit=True, days=int(arg) if arg.isdigit() else 30)


def _cancel_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Отмена", callback_data="pf:back")
    return b.as_markup()


@router.callback_query(F.data == "pf:rem")
async def profile_reminders(callback: CallbackQuery, state: FSMContext):
    uid = callback.message.chat.id
    set_reminders_enabled(uid, not reminders_enabled(uid))
    await callback.answer("Готово")
    await show_profile(callback, state, edit=True, settings=True)


# ---------- справка по разделам и «написать разработчику» ----------

def _help_back_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ К разделам справки", callback_data="help:hub")
    b.button(text="✉️ Написать разработчику", callback_data="help:fb")
    b.adjust(1)
    return b.as_markup()


@router.callback_query(F.data == "help:hub")
async def help_hub(callback: CallbackQuery, state: FSMContext):
    import helpdata
    from keyboards import help_hub_kb
    await state.clear()
    await callback.answer()
    try:
        await callback.message.edit_text(helpdata.INTRO, reply_markup=help_hub_kb(), parse_mode=None)
    except Exception:  # noqa: BLE001 — сообщение старое/не наше
        await callback.message.answer(helpdata.INTRO, reply_markup=help_hub_kb(), parse_mode=None)


@router.callback_query(F.data.startswith("help:s:"))
async def help_section(callback: CallbackQuery):
    import helpdata
    item = helpdata.SECTION_BY_KEY.get(callback.data.split(":", 2)[2])
    if not item:
        await callback.answer("Раздел не найден")
        return
    await callback.answer()
    usage.event("help_open", callback.message.chat.id)
    await callback.message.edit_text(f"{item[0]}\n\n{item[1]}", reply_markup=_help_back_kb(), parse_mode=None)


def _feedback_kind_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💡 Идея", callback_data="fb:k:idea")
    b.button(text="🐞 Ошибка", callback_data="fb:k:bug")
    b.button(text="❓ Вопрос", callback_data="fb:k:q")
    b.button(text="⬅️ Отмена", callback_data="help:hub")
    b.adjust(3, 1)
    return b.as_markup()


@router.callback_query(F.data == "help:fb")
async def feedback_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await ui.show(callback, "✉️ Что вы хотите отправить разработчику?", _feedback_kind_kb(), mode="edit")


@router.message(Command("feedback"))
async def cmd_feedback(message: Message, state: FSMContext):
    await state.clear()
    await ui.show(message, "✉️ Что вы хотите отправить разработчику?", _feedback_kind_kb(), mode="fresh")


@router.callback_query(F.data.startswith("fb:k:"))
async def feedback_kind(callback: CallbackQuery, state: FSMContext):
    kind = callback.data.split(":", 2)[2]
    if kind not in accounts.FEEDBACK_KINDS:
        await callback.answer()
        return
    await state.set_state(FeedbackForm.text)
    await state.update_data(fb_kind=kind)
    await callback.answer()
    hint = {"bug": "Опишите, что случилось и что вы нажимали.", "idea": "Опишите, что хотелось бы добавить или изменить.",
            "q": "Напишите вопрос."}[kind]
    await callback.message.edit_text(f"{hint}\n\nОтправьте одним сообщением (до 1500 символов). Пароли и платёжные данные не пишите.",
                                     reply_markup=_cancel_kb_help(), parse_mode=None)


def _cancel_kb_help() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Отмена", callback_data="help:hub")
    return b.as_markup()


@router.message(FeedbackForm.text, F.text)
async def feedback_text(message: Message, state: FSMContext):
    import feedback
    data = await state.get_data()
    kind = data.get("fb_kind", "idea")
    uid = message.chat.id
    await ui.drop(message)
    try:
        fid = accounts.feedback_add(uid, kind, message.text, "", "bot")
    except ValueError as e:
        await ui.show(message, f"😔 {e}", _cancel_kb_help(), mode="tracked")
        if "много" in str(e):
            await state.clear()
        return
    await state.clear()
    usage.event("feedback", uid)
    username = message.from_user.username if message.from_user else ""
    await feedback.notify_bot(message.bot, feedback.admin_text(fid, kind, "bot", uid, message.text, "", username or ""))
    await ui.show(message, "✅ Спасибо! Сообщение отправлено разработчику.", _help_back_kb(), mode="tracked")


# ---------- данные профиля: согласие, «не использовать», цель, вес ----------

async def _need_consent(callback: CallbackQuery) -> bool:
    if accounts.consent_get(callback.message.chat.id):
        return True
    await callback.answer("Сначала нажми «Согласен»", show_alert=True)
    return False


@router.callback_query(F.data == "pf:consent")
async def profile_consent(callback: CallbackQuery, state: FSMContext):
    accounts.consent_set(callback.message.chat.id)
    usage.event("profile_consent", callback.message.chat.id)
    await callback.answer("Спасибо!")
    await show_profile(callback, state, edit=True)


@router.callback_query(F.data == "pf:trial")
async def profile_trial(callback: CallbackQuery, state: FSMContext):
    uid = callback.message.chat.id
    until = plans.maybe_start_trial(uid)
    if not until:
        await callback.answer("Пробный период уже использован", show_alert=True)
        return
    usage.event("trial_start", uid)
    await callback.answer(f"🎁 Подключил тариф «{plans.PREMIUM_NAME}» на {int(plans.cfg('TRIAL_DAYS') or 0)} дней: "
                          "пробуй все функции приложения.", show_alert=True)
    await show_profile(callback, state, edit=True, settings=True)


@router.callback_query(F.data == "pf:never")
async def profile_never(callback: CallbackQuery, state: FSMContext):
    if not await _need_consent(callback) or not await _need_memory(callback):
        return
    await state.set_state(ProfileForm.never)
    await callback.answer()
    await callback.message.edit_text(
        "🚫 Напиши через запятую, какие продукты никогда не использовать, например: «кинза, грибы, печень». "
        "Я исключу их из всех рецептов. Чтобы очистить список, напиши «нет». Диагнозы писать не нужно.",
        reply_markup=_cancel_kb(), parse_mode=None)


@router.message(ProfileForm.never, F.text)
async def profile_set_never(message: Message, state: FSMContext):
    uid = message.chat.id
    await ui.drop(message)
    if not accounts.consent_get(uid) or not plans.has_feature(uid, "memory"):
        await state.clear()
        return
    g = accounts.goals_get(uid)
    clear = message.text.strip().lower() in ("нет", "очистить", "убрать")
    accounts.goals_set(uid, never="" if clear else message.text,
                       feats=[f for f in g["feats"] if f != "never"] + ([] if clear else ["never"]))
    usage.event("profile_edit", uid)
    await show_profile(message, state, mode="tracked", settings=True)


@router.callback_query(F.data == "pf:goal")
async def profile_goal(callback: CallbackQuery, state: FSMContext):
    if not await _need_consent(callback):
        return
    await state.set_state(ProfileForm.goal)
    await callback.answer()
    await callback.message.edit_text(
        "🎯 Напиши цель на день: калории и, если хочешь, белки, жиры и углеводы в граммах. Например: "
        "«2000 ккал, белки 120, жиры 70, углеводы 200» или просто «1800». Чтобы убрать цель, напиши «нет».",
        reply_markup=_cancel_kb(), parse_mode=None)


@router.message(ProfileForm.goal, F.text)
async def profile_set_goal(message: Message, state: FSMContext):
    uid = message.chat.id
    if not accounts.consent_get(uid):
        await state.clear()
        return
    try:
        fields = accounts.parse_goal(message.text)
        g = accounts.goals_get(uid)
        cleared = all(v is None for v in fields.values())
        accounts.goals_set(uid, **fields, feats=[f for f in g["feats"] if f != "macro"] + ([] if cleared else ["macro"]))
    except ValueError as e:
        await ui.show(message, f"😔 {e}", _cancel_kb(), mode="tracked")
        return
    usage.event("profile_edit", uid)
    await show_profile(message, state, mode="tracked", settings=True)


@router.callback_query(F.data == "pf:weight")
async def profile_weight(callback: CallbackQuery, state: FSMContext):
    if not await _need_consent(callback):
        return
    await state.set_state(ProfileForm.weight)
    await callback.answer()
    await callback.message.edit_text(
        "⚖️ Напиши свой вес в кг на сегодня, например «82.5». Цель по весу: «цель 78», убрать цель: «цель нет». "
        "График за все дни есть на сайте, в разделе «Профиль».",
        reply_markup=_cancel_kb(), parse_mode=None)


@router.message(ProfileForm.weight, F.text)
async def profile_set_weight(message: Message, state: FSMContext):
    uid = message.chat.id
    await ui.drop(message)
    if not accounts.consent_get(uid):
        await state.clear()
        return
    text = message.text.strip().lower()
    try:
        m = re.match(r"цел\w*\s*(нет|[\d.,]+)", text)
        if m:
            g = accounts.goals_get(uid)
            accounts.goals_set(uid, weight_goal=None if m.group(1) == "нет" else m.group(1),
                               feats=g["feats"] if "weight" in g["feats"] else g["feats"] + ["weight"])
        else:
            accounts.weight_set(uid, text.replace("кг", "").strip())
            g = accounts.goals_get(uid)
            if "weight" not in g["feats"]:
                accounts.goals_set(uid, feats=g["feats"] + ["weight"])
    except ValueError as e:
        await ui.show(message, f"😔 {e}. Напиши число, например 82.5", _cancel_kb(), mode="tracked")
        return
    usage.event("weight_add", uid)
    await show_profile(message, state, mode="tracked")


@router.callback_query(F.data == "pf:del")
async def profile_delete_ask(callback: CallbackQuery):
    b = InlineKeyboardBuilder()
    b.button(text="🗑 Да, удалить", callback_data="pf:delyes")
    b.button(text="Отмена", callback_data="pf:back")
    b.adjust(1)
    await callback.answer()
    await callback.message.edit_text(
        "Удалить список «не использовать», цель КБЖУ, вес, полку и согласие? Избранное и подписка останутся.",
        reply_markup=b.as_markup(), parse_mode=None)


@router.callback_query(F.data == "pf:delyes")
async def profile_delete_yes(callback: CallbackQuery, state: FSMContext):
    accounts.delete_my_data(callback.message.chat.id)
    usage.event("profile_delete", callback.message.chat.id)
    await callback.answer("Удалено")
    await show_profile(callback, state, edit=True)


# ---------- функции профиля: включаются галочками ----------
FEATURE_LABELS = {"diary": "📒 Дневник питания", "shelf": "🏠 Полка (что есть дома)", "never": "🚫 Список «не использовать»",
                  "macro": "🎯 Цель КБЖУ", "weight": "⚖️ Вес и график"}
NEEDS_MEMORY = {"shelf", "never"}


def _fts_view(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    g = accounts.goals_get(uid)
    mem = plans.has_feature(uid, "memory")
    b = InlineKeyboardBuilder()
    for key, label in FEATURE_LABELS.items():
        mark = "✅" if key in g["feats"] else ("💎" if key in NEEDS_MEMORY and not mem else "⬜")
        b.button(text=f"{mark} {label}", callback_data=f"pf:ft:{key}")
    b.button(text="⬅️ Профиль", callback_data="pf:back")
    b.adjust(1)
    return ("⚙️ Функции профиля\n\nНажмите, чтобы включить или выключить. Ничего не включается само: данные сохраняются "
            "только для включённых функций.\n💎 значит, что функция входит в тариф «Повар»."), b.as_markup()


@router.callback_query(F.data == "pf:fts")
async def fts_open(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await _need_consent(callback):
        return
    await callback.answer()
    text, markup = _fts_view(callback.message.chat.id)
    await callback.message.edit_text(text, reply_markup=markup, parse_mode=None)


@router.callback_query(F.data.startswith("pf:ft:"))
async def fts_toggle(callback: CallbackQuery):
    uid = callback.message.chat.id
    key = callback.data.split(":", 2)[2]
    if key not in FEATURE_LABELS or not await _need_consent(callback):
        await callback.answer()
        return
    if key in NEEDS_MEMORY and not plans.has_feature(uid, "memory"):
        await callback.answer("Эта функция — в тарифе «Повар» 💎", show_alert=True)
        return
    feats = set(accounts.goals_get(uid)["feats"])
    feats ^= {key}
    accounts.goals_set(uid, feats=sorted(feats))
    await callback.answer("Включено" if key in feats else "Выключено")
    text, markup = _fts_view(uid)
    await callback.message.edit_text(text, reply_markup=markup, parse_mode=None)


# ---------- полка ----------

def shelf_view(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    items = accounts.shelf_list(uid)
    b = InlineKeyboardBuilder()
    if items:
        text = ("🏠 *Моя полка* — долгоживущие продукты, которые у тебя скорее всего есть "
                "(купил или отметил «есть дома»). В списках покупок они идут отдельно.\n\n"
                "Нажми на продукт, если он закончился.")
        for i, it in enumerate(items[:40]):
            b.button(text=f"❌ {it['item'][:30]} · {time.strftime('%d.%m', time.localtime(it['ts']))}",
                     callback_data=f"pf:shdel:{i}")
    else:
        text = ("🏠 *Моя полка* пока пустая.\n\nСюда попадают масло, специи, крупы и другие долгоживущие продукты, "
                "когда ты отмечаешь их в списке покупок «✅ купил» или «🏠 есть дома». Тогда в следующих "
                "рецептах они будут отдельным списком — не придётся покупать второй раз.")
    b.adjust(2)
    b.row(InlineKeyboardButton(text="➕ Добавить (текстом или фото)" if plans.feature_on(uid, "photo_shelf")
                               else "➕ Добавить продукт", callback_data="pf:shadd"))
    b.row(InlineKeyboardButton(text="⬅️ Профиль", callback_data="pf:back"))
    return text, b.as_markup()


@router.callback_query(F.data == "pf:shelf")
async def shelf_open(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await _need_shelf(callback):
        return
    await callback.answer()
    text, markup = shelf_view(callback.message.chat.id)
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("pf:shdel:"))
async def shelf_delete(callback: CallbackQuery):
    uid = callback.message.chat.id
    items = accounts.shelf_list(uid)
    try:
        item = items[int(callback.data.split(":")[2])]["item"]
    except (ValueError, IndexError):
        await callback.answer("Список обновился", show_alert=False)
    else:
        accounts.shelf_remove(uid, item)
        await callback.answer(f"Убрал: {item}")
    text, markup = shelf_view(uid)
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "pf:shadd")
async def shelf_add_start(callback: CallbackQuery, state: FSMContext):
    if not await _need_shelf(callback):
        return
    await state.set_state(ProfileForm.shelf_add)
    await callback.answer()
    uid = callback.message.chat.id
    text = "➕ Напиши продукты через запятую — например: «оливковое масло, куркума, рис»."
    if plans.feature_on(uid, "photo_shelf"):
        text += "\n\n📷 Или пришли фото полки или холодильника — найду продукты, ты отметишь нужные."
    if plans.feature_on(uid, "voice"):
        text += "\n🎙 Можно и голосом."
    await callback.message.edit_text(text, reply_markup=_cancel_kb(), parse_mode=None)


@router.message(ProfileForm.shelf_add, F.text)
async def shelf_add_text(message: Message, state: FSMContext):
    added = [x.strip() for x in message.text.split(",") if x.strip()][:20]
    for x in added:
        accounts.shelf_add(message.chat.id, x)
    await state.clear()
    text, markup = shelf_view(message.chat.id)
    await message.answer(text, reply_markup=markup)


# ---------- вход на сайт ----------

@router.callback_query(F.data == "pf:site")
async def profile_site(callback: CallbackQuery):
    await callback.answer()
    if not config.WEB_APP_URL:
        await callback.message.answer("Сайт пока не настроен.", parse_mode=None)
        return
    code = accounts.new_login_code(callback.message.chat.id)
    b = InlineKeyboardBuilder()
    b.button(text="🌐 Открыть сайт (вход выполнен)", url=f"{config.WEB_APP_URL.rstrip('/')}/?login={code}")
    await callback.message.answer(
        "Ссылка одноразовая и действует 10 минут — не пересылай её никому.\n\n"
        "Профиль, избранное, полка и подписка на сайте будут те же, что здесь. "
        "Если откроешь сайт в другом браузере — нажми там «Войти через Telegram».",
        reply_markup=b.as_markup(), parse_mode=None)


async def handle_start_args(message: Message, args: str | None) -> bool:
    """/start с параметром: link_<код> — вход на сайт, ref_<метка> — откуда пришёл, pay — подписка.
    True — параметр обработан (главное меню показывать не нужно)."""
    if not args:
        return False
    uid = message.chat.id
    if args.startswith("link_"):
        code = args[5:]
        if not accounts.link_code_valid(code):
            await message.answer("😔 Ссылка для входа устарела или уже использована. Нажми «Войти через Telegram» "
                                 "на сайте ещё раз.", parse_mode=None)
            return True
        b = InlineKeyboardBuilder()
        b.button(text="✅ Да, войти на сайте", callback_data=f"pf:lk:{code}")
        b.button(text="Нет", callback_data="pf:lkno")
        b.adjust(1)
        await message.answer("🌐 Войти на сайте под твоим профилем?\n\nЕсли ты сам только что нажал «Войти через "
                             "Telegram» на сайте — подтверждай. Если ссылку тебе кто-то прислал — нажми «Нет»: "
                             "иначе он получит доступ к твоему профилю.", reply_markup=b.as_markup(), parse_mode=None)
        return True
    if args.startswith("ref_"):
        accounts.set_ref(uid, args[4:])
        return False
    if args == "pay":
        await show_subscription(message)
        return True
    return False


@router.callback_query(F.data.startswith("pf:lk:"))
async def link_confirm(callback: CallbackQuery):
    dev = accounts.use_link_code(callback.data[6:], callback.message.chat.id)
    if dev:
        usage.event("site_link", callback.message.chat.id)
    await callback.answer()
    if dev:
        await callback.message.edit_text("✅ Готово! Сайт привязан к твоему профилю — избранное, профиль, полка и "
                                         "подписка теперь общие. Можно вернуться в браузер.", parse_mode=None)
    else:
        await callback.message.edit_text("😔 Ссылка для входа устарела. Нажми «Войти через Telegram» на сайте ещё раз.",
                                         parse_mode=None)


@router.callback_query(F.data == "pf:lkno")
async def link_reject(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text("Хорошо, вход отменён.", parse_mode=None)


@router.callback_query(F.data == "pf:unall")
async def unlink_all(callback: CallbackQuery, state: FSMContext):
    n = accounts.unlink_all(callback.message.chat.id)
    await callback.answer(f"Отвязано устройств: {n}", show_alert=True)
    await show_profile(callback, state, edit=True, settings=True)


# ---------- подписка и оплата ----------

def _sub_view(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["💎 *Подписка*", "", plans.status_text(uid), "",
             "Что даёт подписка:",
             f"• {plans.limits_of('premium').get('recipe', (0, 1))[0]} блюд в день, меню на день и планы на 3 дня",
             "• замены продуктов, корзина ВкусВилла с ценами, PDF для чтения без интернета",
             "• сайт без рекламы, всё общее с ботом", ""]
    b = InlineKeyboardBuilder()
    for o in plans.offers():
        if config.PAYMENT_STARS_ENABLED and o["stars"]:
            b.button(text=f"⭐ {o['title']} — {o['stars']} звёзд", callback_data=f"pf:buy:stars:{o['key']}")
        if config.PAYMENT_PROVIDER_TOKEN and o["rub"]:
            b.button(text=f"💳 {o['title']} — {o['rub']} ₽", callback_data=f"pf:buy:rub:{o['key']}")
        if payanyway.enabled() and o["rub"]:
            b.button(text=f"💳 Картой или СБП: {o['title']} — {o['rub']} ₽", callback_data=f"pf:pw:{o['key']}")
    chk = plans.check_offer()
    if payanyway.enabled() and chk and plans.is_tester(uid):
        b.button(text=f"🧪 {chk['title']} — {chk['rub']} ₽", callback_data=f"pf:pw:{chk['key']}")
    if not config.PAYMENT_STARS_ENABLED and not config.PAYMENT_PROVIDER_TOKEN and not payanyway.enabled():
        lines.append("Оплата скоро появится 🙂")
    b.button(text="🎟 Промокод", callback_data="pf:promo")
    b.button(text="⬅️ Профиль", callback_data="pf:back")
    b.adjust(1)
    return "\n".join(lines), b.as_markup()


async def show_subscription(message: Message, edit: bool = False):
    text, markup = _sub_view(message.chat.id)
    if edit:
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except Exception:  # noqa: BLE001
            pass
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "pf:sub")
async def sub_open(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    usage.event("sub_view", callback.message.chat.id)
    await callback.answer()
    await show_subscription(callback.message, edit=True)


@router.callback_query(F.data.startswith("pf:pw:"))
async def sub_pay_pw(callback: CallbackQuery):
    """Оплата картой или СБП через PayAnyWay: бот присылает кнопку-ссылку на страницу оплаты."""
    uid = callback.message.chat.id
    try:
        order = payanyway.create_order(uid, callback.data.split(":", 2)[2])
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
        return
    await callback.answer()
    usage.event("pay_start", uid)
    b = InlineKeyboardBuilder()
    b.button(text="💳 Перейти к оплате", url=(config.WEB_APP_URL or "https://menu.ymayaka.ru").rstrip("/") + "/pay/" + order["id"])
    b.button(text="⬅️ Подписка", callback_data="pf:sub")
    b.adjust(1)
    await ui.show(callback, f"Оплата: {order['title']}, {order['amount']} ₽.\n\nНажмите кнопку, оплатите картой или через СБП. Подписка включится сама "
                  "в течение минуты, я напишу сюда. Ссылка действует сутки.", b.as_markup(), mode="edit")


@router.callback_query(F.data.startswith("pf:buy:"))
async def sub_buy(callback: CallbackQuery):
    _, _, method, key = callback.data.split(":", 3)
    o = plans.offer(key)
    if not o or method not in ("stars", "rub"):
        await callback.answer("Тариф не найден", show_alert=True)
        return
    await callback.answer()
    payload = f"sub:{key}:{callback.message.chat.id}"
    title = f"Кухонный помощник — {o['title'].lower()}"
    desc = f"Полный доступ на {o['days']} дн.: больше блюд, меню на день и планы, замены, корзина с ценами, PDF."
    if method == "stars":
        if not config.PAYMENT_STARS_ENABLED:
            return
        await callback.bot.send_invoice(callback.message.chat.id, title=title, description=desc, payload=payload,
                                        currency="XTR", prices=[LabeledPrice(label=o["title"], amount=o["stars"])],
                                        provider_token="")
    else:
        if not config.PAYMENT_PROVIDER_TOKEN:
            return
        await callback.bot.send_invoice(callback.message.chat.id, title=title, description=desc, payload=payload,
                                        currency="RUB", prices=[LabeledPrice(label=o["title"], amount=o["rub"] * 100)],
                                        provider_token=config.PAYMENT_PROVIDER_TOKEN, need_email=True,
                                        send_email_to_provider=True)


def _parse_payload(payload: str) -> tuple[dict, int] | None:
    try:
        kind, key, uid = payload.split(":")
        o = plans.offer(key)
        return (o, int(uid)) if kind == "sub" and o else None
    except ValueError:
        return None


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    # Telegram ждёт ответ не дольше 10 секунд — только быстрая проверка, без сети
    ok = _parse_payload(query.invoice_payload) is not None
    await query.answer(ok=ok, error_message=None if ok else "Тариф устарел — открой «Подписку» заново.")


@router.message(F.successful_payment)
async def payment_done(message: Message):
    sp = message.successful_payment
    parsed = _parse_payload(sp.invoice_payload)
    if not parsed:
        logger.error("Оплата с непонятным payload %r", sp.invoice_payload)
        return
    o, uid = parsed
    until = accounts.apply_payment(sp.telegram_payment_charge_id, uid, sp.total_amount, sp.currency, o["days"], o["key"])
    if until is None:
        return        # этот платёж уже зачислен (повторная доставка)
    usage.event("payment", uid)
    await message.answer(f"🎉 Спасибо! Подписка действует до {time.strftime('%d.%m.%Y', time.localtime(until))}.",
                         parse_mode=None)
    for admin in config.ADMIN_TG_IDS:
        try:
            await message.bot.send_message(admin, f"💰 Оплата: {sp.total_amount} {sp.currency}, тариф {o['key']}, "
                                                  f"пользователь {uid}", parse_mode=None)
        except Exception:  # noqa: BLE001
            pass


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message):
    """Обязательная для оплаты звёздами команда поддержки по платежам."""
    await message.answer("💬 Вопросы по оплате и возврату: напиши сюда, в этот чат, что случилось и когда платил — "
                         "мы ответим и при необходимости вернём деньги. Статус подписки — /profile.", parse_mode=None)


@router.callback_query(F.data == "pf:promo")
async def promo_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ProfileForm.promo)
    await callback.answer()
    await ui.show(callback, "🎟 Напиши промокод:", _cancel_kb(), mode="edit")


@router.message(ProfileForm.promo, F.text)
async def promo_enter(message: Message, state: FSMContext):
    code = message.text.strip()[:32]
    days = plans.promo_days(code)
    await ui.drop(message)
    await state.clear()
    if not days:
        note = "😔 Такого промокода нет."
    elif not accounts.use_promo(message.chat.id, code):
        note = "Этот промокод ты уже использовал 🙂"
    else:
        until = accounts.grant_days(message.chat.id, days, "promo:" + code.upper())
        usage.event("promo", message.chat.id)
        note = f"🎉 Промокод принят! Полный доступ до {time.strftime('%d.%m.%Y', time.localtime(until))}."
    await ui.show(message, note, _cancel_kb(), mode="tracked")


@router.message(Command("grant"))
async def cmd_grant(message: Message, command: CommandObject):
    """Админ: /grant <id в Telegram> <дней> — выдать подписку вручную (например, блогеру)."""
    if message.chat.id not in config.ADMIN_TG_IDS:
        return
    try:
        uid, days = (command.args or "").split()
        until = accounts.grant_days(int(uid), int(days), "admin")
    except ValueError:
        await message.answer("Формат: /grant 123456789 30", parse_mode=None)
        return
    await message.answer(f"Готово: {uid} до {time.strftime('%d.%m.%Y', time.localtime(until))}", parse_mode=None)
