"""Итог недели в Telegram: что подобрал, сколько готовил, дневник и вес, один совет.

Приходит в воскресенье вечером только тем, кто за неделю что-то делал. Под сообщением «Не присылать итоги»,
включить обратно — в Настройках. Отметка «уже отправлен на этой неделе» не даёт прислать дважды.
"""
import asyncio
import datetime
import logging
import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import accounts
import config
import storage
import usage

logger = logging.getLogger(__name__)
router = Router()

WEEK = 7 * 86400


def enabled(uid) -> bool:
    return not (storage.get_last_request(uid) or {}).get("digest_off")


def set_enabled(uid, on: bool) -> None:
    storage._update(uid, digest_off=None if on else True)


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def candidates(since: float) -> list[int]:
    """Кто за неделю подбирал рецепты, готовил или вёл дневник (только Telegram-аккаунты)."""
    ids = {r[0] for r in usage.rows("SELECT DISTINCT uid FROM gens WHERE ts>? AND src<>'reminder'", (since,))}
    ids |= {r[0] for r in usage.rows("SELECT DISTINCT uid FROM events WHERE ts>? AND name IN ('cook_start','diary_add')", (since,))}
    day = datetime.date.fromtimestamp(since).isoformat()
    ids |= {r[0] for r in accounts._q("SELECT DISTINCT uid FROM diary WHERE day>=?", (day,), all_=True) or []}
    out = []
    for u in ids:
        try:
            n = int(u)
        except (TypeError, ValueError):
            continue
        if 0 < n < accounts.VK_OFFSET:
            out.append(n)
    return sorted(out)


def build(uid: int, now: float | None = None) -> str | None:
    """Текст итога или None, если за неделю ничего не было."""
    now = now or time.time()
    since = now - WEEK
    kinds = dict(usage.rows("SELECT kind, COUNT(*) FROM gens WHERE uid=? AND ts>? AND src<>'reminder' GROUP BY kind", (str(uid), since)))
    cooked = usage.rows("SELECT COUNT(*) FROM events WHERE uid=? AND ts>? AND name='cook_start'", (str(uid), since))[0][0]
    end = datetime.date.fromtimestamp(now).isoformat()
    st = accounts.diary_stats(accounts.diary_series(uid, 7, end))
    g = accounts.goals_get(uid)
    week_ago = datetime.date.fromtimestamp(since).isoformat()
    ws = [w for w in accounts.weights_list(uid, 60) if w["day"] >= week_ago]

    recipes = kinds.get("recipe", 0)
    menus = kinds.get("daymenu", 0)
    plans_n = kinds.get("week", 0)
    if not (recipes or menus or plans_n or cooked or st["logged"] or len(ws) > 1):
        return None
    lines = ["📊 Ваша неделя", ""]
    got = []
    if recipes:
        got.append(f"{recipes} {_plural(recipes, 'рецепт', 'рецепта', 'рецептов')}")
    if menus:
        got.append(f"{menus} {_plural(menus, 'меню', 'меню', 'меню')} на день")
    if plans_n:
        got.append(f"{plans_n} {_plural(plans_n, 'план', 'плана', 'планов')} на несколько дней")
    if got:
        lines.append("🍳 Подобрано: " + ", ".join(got))
    if cooked:
        lines.append(f"👨‍🍳 Готовили по шагам с таймерами: {cooked} {_plural(cooked, 'раз', 'раза', 'раз')}")
    if st["logged"]:
        line = f"📔 Дневник: {st['logged']} из 7 дней, в среднем {st['avg_kcal']} ккал в день"
        if g.get("kcal"):
            diff = st["avg_kcal"] - g["kcal"]
            line += f" (цель {g['kcal']}" + (", в пределах нормы)" if abs(diff) <= g["kcal"] * 0.1 else f", {'+' if diff > 0 else '−'}{abs(diff)})")
        lines.append(line)
        if st["avg_protein"]:
            lines.append(f"     белки {st['avg_protein']} г · жиры {st['avg_fat']} г · углеводы {st['avg_carb']} г")
    if len(ws) > 1:
        d = round(ws[-1]["kg"] - ws[0]["kg"], 1)
        lines.append(f"⚖️ Вес: {ws[0]['kg']:g} → {ws[-1]['kg']:g} кг" + (f" ({'+' if d > 0 else '−'}{abs(d):g})" if d else " (без изменений)"))
    lines += ["", "💡 " + _tip(recipes + menus, cooked, st, g)]
    return "\n".join(lines)


def _tip(picked: int, cooked: int, st: dict, g: dict) -> str:
    if st["logged"] and g.get("kcal") and st["avg_kcal"] > g["kcal"] * 1.1:
        return "В среднем выходит больше цели. Тип питания «По КБЖУ» подберёт блюда под вашу норму."
    if st["logged"] and g.get("protein") and st["avg_protein"] < g["protein"] * 0.8:
        return "Белка меньше цели. В «Тип питания» есть «Спорт: сила» — там блюда с упором на белок."
    if not st["logged"]:
        return "Попробуйте дневник питания: еду можно записывать словами, например «2 яйца и бутерброд»."
    if picked >= 3:
        return "В «Любимых» можно собрать общий список покупок на несколько рецептов: одинаковые продукты сложатся."
    if picked and not cooked:
        return "Нажмите «Готовить» под рецептом: шаги по одному и таймеры сами напомнят, когда снимать с огня."
    return "Загляните в книгу рецептов: готовые блюда без ожидания, с таймерами и списком покупок."


def keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Книга рецептов", callback_data="book:open")],
        [InlineKeyboardButton(text="🔕 Не присылать итоги", callback_data="dg:off")],
    ])


def _week_key(uid, now: float) -> str:
    y, w, _ = datetime.date.fromtimestamp(now).isocalendar()
    return f"digest:{uid}:{y}-{w}"


async def job(bot) -> int:
    now = time.time()
    sent = 0
    for uid in candidates(now - WEEK):
        try:
            if not enabled(uid) or accounts.sess_get(_week_key(uid, now)):
                continue
            text = build(uid, now)
            if not text:
                continue
            accounts.sess_set(_week_key(uid, now), {"ts": now})
            await bot.send_message(uid, text, reply_markup=keyboard(), parse_mode=None)
            sent += 1
            await asyncio.sleep(0.1)       # не упираться в лимиты Telegram на рассылку
        except Exception as e:  # noqa: BLE001 — заблокировал бота и т. п.: остальные получат
            logger.info("Итог недели %s не отправлен: %s", uid, e)
    logger.info("Итог недели отправлен: %d", sent)
    return sent


@router.callback_query(F.data == "dg:off")
async def digest_off(callback: CallbackQuery):
    set_enabled(callback.message.chat.id, False)
    await callback.answer("Больше не пришлю. Включить снова: Профиль → Настройки → «Итог недели».", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📖 Книга рецептов", callback_data="book:open")]]))
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data == "pf:dg")
async def digest_toggle(callback: CallbackQuery, state: FSMContext):
    import bot_profile
    uid = callback.message.chat.id
    set_enabled(uid, not enabled(uid))
    await callback.answer("Итог недели: " + ("включён" if enabled(uid) else "выключен"))
    await bot_profile.show_profile(callback, state, edit=True, settings=True)


@router.message(Command("digest"), lambda m: m.chat.id in config.ADMIN_TG_IDS)
async def digest_preview(message: Message):
    """Посмотреть свой итог недели прямо сейчас (для проверки, только владельцу)."""
    text = build(message.chat.id) or "За неделю пока нечего подвести."
    await message.answer(text, reply_markup=keyboard(), parse_mode=None)
