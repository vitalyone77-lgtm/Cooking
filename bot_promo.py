"""
/promo — недельные задания по продвижению для владельца (ADMIN_TG_IDS): шаги, готовые тексты, картинки, отметки «сделано».
Раз в неделю (понедельник утром) бот сам присылает задания текущей недели. Отметки хранятся в sess и не сгорают.
"""
import datetime
import logging
import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import accounts
import config
import promo_plan as plan

logger = logging.getLogger(__name__)
router = Router(name="promo")
_KEY = "promo:state"
_ROOT = os.path.dirname(os.path.abspath(__file__))
MARK = {"done": "✅", "skip": "⏭", None: "⬜"}


def _is_admin(chat_id: int) -> bool:
    return chat_id in config.ADMIN_TG_IDS


def _state() -> dict:
    st = accounts.sess_get(_KEY) or {}
    st.setdefault("marks", {})
    return st


def _save(st: dict) -> None:
    accounts.sess_set(_KEY, st)
    accounts.sess_mark_forever(_KEY)


def current_week(st: dict | None = None) -> int | None:
    """Первая неделя, где остались невыполненные задания; None, если всё пройдено."""
    marks = (st or _state())["marks"]
    for w in plan.WEEKS:
        if any(t["id"] not in marks for t in plan.TASKS if t["week"] == w):
            return w
    return None


def _spent(st: dict) -> int:
    return sum(t["cost"] for t in plan.TASKS if st["marks"].get(t["id"]) == "done")


def _week_text(week: int, st: dict) -> str:
    items = [t for t in plan.TASKS if t["week"] == week]
    lines = [f"📣 Продвижение: неделя {week} из {plan.WEEKS[-1]}", ""]
    for t in items:
        price = f"до {t['cost']} ₽" if t["cost"] else "бесплатно"
        lines.append(f"{MARK[st['marks'].get(t['id'])]} {t['title']} ({price}, ~{t['minutes']} мин)")
    done = sum(1 for t in plan.TASKS if st["marks"].get(t["id"]) == "done")
    lines += ["", f"Выполнено заданий: {done} из {len(plan.TASKS)}. Потрачено: {_spent(st)} из {plan.BUDGET} ₽ запланированных.",
              "Нажмите на задание, чтобы увидеть шаги и готовый текст."]
    return "\n".join(lines)


def _week_kb(week: int, st: dict) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for t in plan.TASKS:
        if t["week"] == week:
            b.button(text=f"{MARK[st['marks'].get(t['id'])]} {t['title'][:45]}", callback_data=f"pr:t:{t['id']}")
    prev_w = next((w for w in reversed(plan.WEEKS) if w < week), None)
    next_w = next((w for w in plan.WEEKS if w > week), None)
    nav = ([("⬅ Неделя " + str(prev_w), f"pr:w:{prev_w}")] if prev_w else []) + ([("Неделя " + str(next_w) + " ➡", f"pr:w:{next_w}")] if next_w else [])
    for text, data in nav:
        b.button(text=text, callback_data=data)
    b.adjust(*([1] * sum(1 for t in plan.TASKS if t["week"] == week)), len(nav) or 1)
    return b.as_markup()


def task_text(t: dict, st: dict) -> str:
    status = {"done": "✅ сделано", "skip": "⏭ пропущено"}.get(st["marks"].get(t["id"]), "⬜ не сделано")
    price = f"до {t['cost']} ₽" if t["cost"] else "бесплатно"
    lines = [f"📌 {t['title']}", f"{price} · ~{t['minutes']} мин · {status}", "", f"Зачем: {t['why']}", f"Где: {t['where']}", "", "Шаги:"]
    lines += [f"{i}. {s}" for i, s in enumerate(t["steps"], 1)]
    if t["text"]:
        lines += ["", "Готовый текст (скопируйте):", "──────────", t["text"], "──────────"]
    return "\n".join(lines)[:3900]


def _task_kb(t: dict) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Сделано", callback_data=f"pr:d:{t['id']}")
    b.button(text="⏭ Пропустить", callback_data=f"pr:s:{t['id']}")
    if t["image"]:
        b.button(text="🖼 Получить картинку", callback_data=f"pr:i:{t['id']}")
    b.button(text="⬅ К неделе", callback_data=f"pr:w:{t['week']}")
    b.adjust(2, 1, 1) if t["image"] else b.adjust(2, 1)
    return b.as_markup()


async def _show_week(message: Message, week: int, edit: bool = False) -> None:
    st = _state()
    text, kb = _week_text(week, st), _week_kb(week, st)
    if edit:
        try:
            await message.edit_text(text, reply_markup=kb, parse_mode=None)
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=kb, parse_mode=None)


@router.message(Command("promo"))
async def cmd_promo(message: Message):
    if not _is_admin(message.chat.id):
        return
    week = current_week()
    if week is None:
        await message.answer("🎉 Все задания пройдены или пропущены. Напишите мне итоги, составим план на следующий месяц.\n"
                             "Чтобы пройти заново, нажмите /promo_reset.", parse_mode=None)
        return
    await _show_week(message, week)


@router.message(Command("promo_reset"))
async def cmd_reset(message: Message):
    if _is_admin(message.chat.id):
        _save({"marks": {}})
        await message.answer("Отметки сброшены. /promo — начать сначала.", parse_mode=None)


def _guard(callback: CallbackQuery) -> bool:
    return bool(callback.message) and _is_admin(callback.message.chat.id)


@router.callback_query(F.data.startswith("pr:w:"))
async def cb_week(callback: CallbackQuery):
    if not _guard(callback):
        return await callback.answer()
    arg = callback.data.split(":")[2]
    if not arg.isdigit() or int(arg) not in plan.WEEKS:
        return await callback.answer()
    await callback.answer()
    await _show_week(callback.message, int(arg), edit=True)


@router.callback_query(F.data.startswith("pr:t:"))
async def cb_task(callback: CallbackQuery):
    if not _guard(callback):
        return await callback.answer()
    t = plan.BY_ID.get(callback.data.split(":")[2])
    if not t:
        return await callback.answer()
    await callback.answer()
    await callback.message.edit_text(task_text(t, _state()), reply_markup=_task_kb(t), parse_mode=None, disable_web_page_preview=True)


@router.callback_query(F.data.regexp(r"^pr:[ds]:"))
async def cb_mark(callback: CallbackQuery):
    if not _guard(callback):
        return await callback.answer()
    _, kind, tid = callback.data.split(":", 2)
    t = plan.BY_ID.get(tid)
    if not t:
        return await callback.answer()
    st = _state()
    st["marks"][tid] = "done" if kind == "d" else "skip"
    _save(st)
    await callback.answer("Отмечено ✅" if kind == "d" else "Пропущено")
    await _show_week(callback.message, t["week"], edit=True)


@router.callback_query(F.data.startswith("pr:i:"))
async def cb_image(callback: CallbackQuery):
    if not _guard(callback):
        return await callback.answer()
    t = plan.BY_ID.get(callback.data.split(":")[2])
    if not t:
        return await callback.answer()
    sent = 0
    for rel in t["image"]:
        path = os.path.join(_ROOT, rel)
        if os.path.isfile(path):
            await callback.message.answer_document(FSInputFile(path), parse_mode=None)
            sent += 1
    await callback.answer("Файлы выше" if sent else "Картинки не нашлись на сервере", show_alert=not sent)


async def weekly_job(bot) -> None:
    """Понедельник утром: присылаем задания текущей недели каждому владельцу, не чаще раза в календарную неделю."""
    iso = datetime.date.today().isocalendar()
    marker = f"{iso[0]}-{iso[1]}"
    st = _state()
    week = current_week(st)
    if week is None or st.get("sent") == marker:
        return
    for admin in config.ADMIN_TG_IDS:
        try:
            await bot.send_message(admin, "Доброе утро! Задания по продвижению на эту неделю:\n\n" + _week_text(week, st),
                                   reply_markup=_week_kb(week, st), parse_mode=None)
        except Exception as e:
            logger.warning("Не удалось отправить задания %s: %r", admin, e)
    st["sent"] = marker
    _save(st)
