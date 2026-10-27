"""
Личный кабинет в боте: профиль (аллергии, «никогда не использовать»), полка, вход на сайт,
подписка и оплата (Telegram Stars сразу; рубли — когда будет токен платёжного сервиса), промокоды,
ссылки блогеров (/start ref_…), админская выдача подписки (/grant).
"""
import logging
import time

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

import accounts
import config
import plans
from states import ProfileForm
from storage import reminders_enabled, set_reminders_enabled

logger = logging.getLogger(__name__)
router = Router(name="profile")


# ---------- общее: отказ по лимиту / уведомление о пробном периоде ----------

def upsell_kb(uid) -> InlineKeyboardMarkup | None:
    if not plans.can_upsell(uid):
        return None
    b = InlineKeyboardBuilder()
    b.button(text="💎 Подписка", callback_data="pf:sub")
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

def _locked_view(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    """Без подписки бот ничего не запоминает: профиль и полка — функция «memory»."""
    text = ("👤 *Профиль*\n\n" + plans.status_text(uid) + "\n\n"
            "🔒 С подпиской я запоминаю тебя: аллергии и продукты, которые ты не ешь (их не будет ни в одном "
            "рецепте), и «полку» — масло, специи и крупы, которые у тебя уже есть, чтобы не покупать их снова.")
    b = InlineKeyboardBuilder()
    b.button(text="💎 Подписка", callback_data="pf:sub")
    b.button(text="🎟 Промокод", callback_data="pf:promo")
    b.button(text="🌐 Открыть сайт", callback_data="pf:site")
    b.button(text="🔕 Выключить напоминания" if reminders_enabled(uid) else "🔔 Включить напоминания",
             callback_data="pf:rem")
    b.button(text="⬅️ Меню", callback_data="menu:open")
    b.adjust(2, 1, 1, 1)
    return text, b.as_markup()


async def _need_memory(callback: CallbackQuery) -> bool:
    if plans.has_feature(callback.message.chat.id, "memory"):
        return True
    await callback.answer("Профиль и полка — в подписке 💎", show_alert=True)
    return False


def _profile_view(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    if not plans.has_feature(uid, "memory"):
        return _locked_view(uid)
    p = accounts.get_profile(uid)
    shelf = accounts.shelf_list(uid)
    lines = [
        "👤 *Профиль*",
        "",
        f"🚫 Аллергии: {p['allergies'] or 'не указаны'}",
        f"🙅 Никогда не использовать: {p['never'] or 'ничего'}",
        "_Это учитывается в каждом рецепте, меню и плане — в боте и на сайте._",
        "",
        f"🏠 На полке: {len(shelf)} " + _plural(len(shelf), "продукт", "продукта", "продуктов"),
        plans.status_text(uid),
        f"🔔 Напоминания: {'включены' if reminders_enabled(uid) else 'выключены'}",
    ]
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Аллергии", callback_data="pf:edit:allergies")
    b.button(text="✏️ Не использовать", callback_data="pf:edit:never")
    b.button(text="🏠 Моя полка", callback_data="pf:shelf")
    b.button(text="🌐 Открыть сайт", callback_data="pf:site")
    if config.PAYWALL_ENABLED:
        b.button(text="💎 Подписка", callback_data="pf:sub")
        b.button(text="🎟 Промокод", callback_data="pf:promo")
    b.button(text="🔕 Выключить напоминания" if reminders_enabled(uid) else "🔔 Включить напоминания",
             callback_data="pf:rem")
    devs = accounts.devices_of(uid)
    if devs:
        b.button(text=f"🔓 Выйти на сайте ({devs})", callback_data="pf:unall")
    b.button(text="⬅️ Меню", callback_data="menu:open")
    b.adjust(2, 2, *([2] if config.PAYWALL_ENABLED else []), 1, *([1] if devs else []), 1)
    return "\n".join(lines), b.as_markup()


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


async def show_profile(message: Message, state: FSMContext | None = None, edit: bool = False):
    if state:
        await state.clear()
    text, markup = _profile_view(message.chat.id)
    if edit:
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except Exception:  # noqa: BLE001 — сообщение старое/не наше — пришлём новое
            pass
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "profile:open")
async def profile_open(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await show_profile(callback.message, state)


@router.message(Command("profile"))
async def cmd_profile(message: Message, state: FSMContext):
    await show_profile(message, state)


@router.callback_query(F.data == "pf:back")
async def profile_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await show_profile(callback.message, state, edit=True)


def _cancel_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Отмена", callback_data="pf:back")
    return b.as_markup()


_PROMPTS = {
    "allergies": ("🚫 Напиши через запятую, на что у тебя аллергия — например: «орехи, мёд, креветки».\n"
                  "Я не буду использовать эти продукты нигде, даже в соусах. Чтобы очистить — напиши «нет»."),
    "never": ("🙅 Какие продукты ты не ешь и не хочешь видеть в рецептах? Через запятую — например: «кинза, печень, грибы».\n"
              "Чтобы очистить — напиши «нет»."),
}


@router.callback_query(F.data.startswith("pf:edit:"))
async def profile_edit(callback: CallbackQuery, state: FSMContext):
    field = callback.data.split(":")[2]
    if not await _need_memory(callback):
        return
    if field not in _PROMPTS:
        await callback.answer()
        return
    await state.set_state(ProfileForm.allergies if field == "allergies" else ProfileForm.never)
    await callback.answer()
    await callback.message.edit_text(_PROMPTS[field], reply_markup=_cancel_kb(), parse_mode=None)


@router.message(ProfileForm.allergies, F.text)
async def profile_set_allergies(message: Message, state: FSMContext):
    accounts.set_profile(message.chat.id, allergies=message.text)
    await message.answer("✅ Сохранил.", parse_mode=None)
    await show_profile(message, state)


@router.message(ProfileForm.never, F.text)
async def profile_set_never(message: Message, state: FSMContext):
    accounts.set_profile(message.chat.id, never=message.text)
    await message.answer("✅ Сохранил.", parse_mode=None)
    await show_profile(message, state)


@router.callback_query(F.data == "pf:rem")
async def profile_reminders(callback: CallbackQuery, state: FSMContext):
    uid = callback.message.chat.id
    set_reminders_enabled(uid, not reminders_enabled(uid))
    await callback.answer("Готово")
    await show_profile(callback.message, state, edit=True)


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
    b.row(InlineKeyboardButton(text="➕ Добавить продукт", callback_data="pf:shadd"))
    b.row(InlineKeyboardButton(text="⬅️ Профиль", callback_data="pf:back"))
    return text, b.as_markup()


@router.callback_query(F.data == "pf:shelf")
async def shelf_open(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await _need_memory(callback):
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
    if not await _need_memory(callback):
        return
    await state.set_state(ProfileForm.shelf_add)
    await callback.answer()
    await callback.message.edit_text("➕ Напиши продукты через запятую — например: «оливковое масло, куркума, рис».",
                                     reply_markup=_cancel_kb(), parse_mode=None)


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
    await show_profile(callback.message, state, edit=True)


# ---------- подписка и оплата ----------

def _sub_view(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["💎 *Подписка*", "", plans.status_text(uid), "",
             "Что даёт подписка:",
             f"• {plans.LIMITS['premium'].get('recipe', (0, 1))[0]} блюд в день, меню на день и планы на 3 дня",
             "• замены продуктов, корзина ВкусВилла с ценами, PDF для чтения без интернета",
             "• сайт без рекламы, всё общее с ботом", ""]
    b = InlineKeyboardBuilder()
    for o in plans.offers():
        if config.PAYMENT_STARS_ENABLED and o["stars"]:
            b.button(text=f"⭐ {o['title']} — {o['stars']} звёзд", callback_data=f"pf:buy:stars:{o['key']}")
        if config.PAYMENT_PROVIDER_TOKEN and o["rub"]:
            b.button(text=f"💳 {o['title']} — {o['rub']} ₽", callback_data=f"pf:buy:rub:{o['key']}")
    if not config.PAYMENT_STARS_ENABLED and not config.PAYMENT_PROVIDER_TOKEN:
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
    await callback.answer()
    await show_subscription(callback.message, edit=True)


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
    await callback.message.answer("🎟 Напиши промокод:", reply_markup=_cancel_kb(), parse_mode=None)


@router.message(ProfileForm.promo, F.text)
async def promo_enter(message: Message, state: FSMContext):
    code = message.text.strip()[:32]
    days = plans.promo_days(code)
    await state.clear()
    if not days:
        await message.answer("😔 Такого промокода нет.", parse_mode=None)
    elif not accounts.use_promo(message.chat.id, code):
        await message.answer("Этот промокод ты уже использовал 🙂", parse_mode=None)
    else:
        until = accounts.grant_days(message.chat.id, days, "promo:" + code.upper())
        await message.answer(f"🎉 Промокод принят! Полный доступ до {time.strftime('%d.%m.%Y', time.localtime(until))}.",
                             parse_mode=None)
    await show_profile(message)


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
