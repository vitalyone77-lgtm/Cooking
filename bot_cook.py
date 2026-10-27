"""
Режим готовки по шагам: ОДНО сообщение, которое меняется на месте.

Под рецептом кнопка «👨‍🍳 Готовить по шагам» → сообщение «Шаг 2 из 6»: текст текущего шага, кнопка таймера
для этого шага (если в нём есть время), «✅ Готово → дальше», «⬅️ Назад», ингредиенты и все шаги с галочками.
Сработавший таймер — НОВОЕ сообщение (правка сообщения не издаёт звук), оно удаляется, когда шаг отмечен.
Сессия и таймер лежат в SQLite (accounts.sess) — после перезапуска бота продолжают работать (resume_timers).
"""
import asyncio
import logging
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

import accounts
import config
import timers as tm

logger = logging.getLogger(__name__)
router = Router(name="cook")

_HDR = r"^\s*[*_]*{}[*_]*:?[*_]*\s*$"
_ING_RE = re.compile(_HDR.format("Продукты"), re.IGNORECASE | re.MULTILINE)
_STEPS_RE = re.compile(_HDR.format("Инструкция"), re.IGNORECASE | re.MULTILINE)
_STEP_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)")
_BULLET_RE = re.compile(r"^\s*[-•–*]\s+")
_MD_RE = re.compile(r"[*_`]")

_timer_sleep = asyncio.sleep      # подменяется в тестах
_tasks: dict[str, asyncio.Task] = {}


# ---------- разбор рецепта ----------

def parse_recipe(text: str) -> dict:
    """{title, ingredients: [строки], steps: [строки]} из текста рецепта (с разметкой или без)."""
    text = text or ""
    title = ""
    for ln in text.splitlines():
        if "🍽" in ln:
            title = _MD_RE.sub("", ln.replace("🍽", "")).strip()
            break
    ingredients = []
    m = _ING_RE.search(text)
    if m:
        for ln in text[m.end():].splitlines():
            s = ln.strip()
            if _STEPS_RE.match(ln) or (not s and ingredients):
                break
            if _BULLET_RE.match(s):
                ingredients.append(_MD_RE.sub("", _BULLET_RE.sub("", s)).strip())
    steps = []
    m = _STEPS_RE.search(text)
    body = text[m.end():] if m else text
    for ln in body.splitlines():
        sm = _STEP_RE.match(ln)
        if sm:
            steps.append(_MD_RE.sub("", sm.group(2)).strip())
        elif steps and ln.strip() and not ln.strip().startswith(("💡", "ПРОДУКТЫ_СПИСОК")) and len(steps) < 40:
            if ln.startswith((" ", "\t")):          # продолжение шага на следующей строке
                steps[-1] += " " + _MD_RE.sub("", ln).strip()
    return {"title": title[:80], "ingredients": ingredients[:40], "steps": steps[:30]}


# ---------- вид сообщения ----------

def _clock(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, ZoneInfo(config.REMINDER_TIMEZONE)).strftime("%H:%M")
    except Exception:  # noqa: BLE001
        return time.strftime("%H:%M", time.localtime(ts))


def _bar(done: int, total: int) -> str:
    n = 10
    k = round(n * done / total) if total else 0
    return "▰" * k + "▱" * (n - k)


def render(s: dict) -> tuple[str, InlineKeyboardMarkup]:
    steps, i = s["steps"], s["i"]
    total = len(steps)
    done = set(s.get("done", []))
    b = InlineKeyboardBuilder()
    lines = [f"👨‍🍳 {s['title'] or 'Готовим'}"]
    t = s.get("timer")
    if s.get("finished"):
        lines += ["", "🎉 Готово! Приятного аппетита!", "", f"Все шаги пройдены: {total} из {total}."]
        b.button(text="⬅️ К шагам", callback_data="ck:g:0")
        b.button(text="✖ Закрыть", callback_data="ck:x")
        b.adjust(2)
        return "\n".join(lines), b.as_markup()
    lines.append(f"Шаг {i + 1} из {total}   {_bar(len(done), total)}")
    if t and t.get("end", 0) > time.time():
        lines.append(f"⏱ Таймер шага {t['step'] + 1}: сигнал в {_clock(t['end'])}"
                     + (f", максимум в {_clock(t['end_max'])}" if t.get("end_max", 0) > t["end"] else ""))
    view = s.get("view", "step")
    if view == "ing":
        lines += ["", "🧾 Продукты:"] + ([f"• {x}" for x in s["ingredients"]] or ["список продуктов не найден"])
    elif view == "all":
        lines.append("")
        for k, st in enumerate(steps):
            mark = "✅" if k in done else ("▶️" if k == i else "▫️")
            lines.append(f"{mark} {k + 1}. {st if k == i else (st[:70] + ('…' if len(st) > 70 else ''))}")
    else:
        lines += ["", f"{'✅ ' if i in done else ''}{i + 1}. {steps[i]}"]
        if i + 1 < total:
            nxt = steps[i + 1]
            lines += ["", f"Дальше: {nxt[:90] + ('…' if len(nxt) > 90 else '')}"]
    rows = []
    if t and t.get("end_max", t.get("end", 0)) > time.time():
        rows.append([InlineKeyboardButton(text="⏹ Остановить таймер", callback_data="ck:ts")])
    else:
        times = tm.parse_times(steps[i])
        if times:
            rows.append([InlineKeyboardButton(text=f"⏱ Таймер {tm.label(mn, mx)}", callback_data=f"ck:t:{mn}:{mx}")
                         for mn, mx in times])
    nav = []
    if i > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ck:g:{i - 1}"))
    nav.append(InlineKeyboardButton(text="✅ Готово → дальше" if i + 1 < total else "🎉 Готово, всё!",
                                    callback_data=f"ck:d:{i}"))
    rows.append(nav)
    rows.append([
        InlineKeyboardButton(text="📖 Шаг" if view == "ing" else "🧾 Продукты", callback_data="ck:v:" + ("step" if view == "ing" else "ing")),
        InlineKeyboardButton(text="📖 Шаг" if view == "all" else "📋 Все шаги", callback_data="ck:v:" + ("step" if view == "all" else "all")),
    ])
    rows.append([InlineKeyboardButton(text="✖ Закончить", callback_data="ck:x")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- сессия ----------

def _key(chat_id: int, msg_id: int) -> str:
    return f"ck:{chat_id}:{msg_id}"


async def _show(callback: CallbackQuery, s: dict):
    accounts.sess_set(_key(callback.message.chat.id, callback.message.message_id), s)
    text, markup = render(s)
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode=None)
    except Exception as e:  # noqa: BLE001 — «message is not modified» при двойном нажатии
        if "not modified" not in str(e):
            raise


async def _drop_alerts(bot: Bot, chat_id: int, s: dict):
    """Сигналы таймеров, которые уже отработали, убираем — чтобы чат не зарастал."""
    for mid in s.pop("alerts", []):
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:  # noqa: BLE001
            pass


@router.callback_query(F.data == "ck:start")
async def cook_start(callback: CallbackQuery):
    r = parse_recipe(callback.message.text or "")
    if not r["steps"]:
        await callback.answer("Не нашёл шаги в этом рецепте.", show_alert=True)
        return
    await callback.answer()
    s = {**r, "i": 0, "done": [], "view": "step"}
    text, markup = render(s)
    msg = await callback.message.answer(text, reply_markup=markup, parse_mode=None)
    accounts.sess_set(_key(msg.chat.id, msg.message_id), s)


async def _load(callback: CallbackQuery) -> dict | None:
    s = accounts.sess_get(_key(callback.message.chat.id, callback.message.message_id))
    if not s:
        await callback.answer("Этот режим готовки устарел — нажми «Готовить по шагам» под рецептом ещё раз.",
                              show_alert=True)
    return s


@router.callback_query(F.data.startswith("ck:d:"))
async def cook_done(callback: CallbackQuery):
    s = await _load(callback)
    if not s:
        return
    i = int(callback.data.split(":")[2])
    if i != s["i"]:            # старая кнопка (двойное нажатие) — просто показываем текущее
        await callback.answer()
        await _show(callback, s)
        return
    done = set(s.get("done", []))
    done.add(i)
    s["done"] = sorted(done)
    await _drop_alerts(callback.bot, callback.message.chat.id, s)
    if i + 1 < len(s["steps"]):
        s["i"] = i + 1
        s["view"] = "step"
    else:
        s["finished"] = True
    await callback.answer()
    await _show(callback, s)


@router.callback_query(F.data.startswith("ck:g:"))
async def cook_goto(callback: CallbackQuery):
    s = await _load(callback)
    if not s:
        return
    i = int(callback.data.split(":")[2])
    s["i"] = max(0, min(i, len(s["steps"]) - 1))
    s.pop("finished", None)
    s["view"] = "step"
    await callback.answer()
    await _show(callback, s)


@router.callback_query(F.data.startswith("ck:v:"))
async def cook_view(callback: CallbackQuery):
    s = await _load(callback)
    if not s:
        return
    v = callback.data.split(":")[2]
    s["view"] = v if v in ("step", "ing", "all") else "step"
    await callback.answer()
    await _show(callback, s)


@router.callback_query(F.data == "ck:x")
async def cook_close(callback: CallbackQuery):
    key = _key(callback.message.chat.id, callback.message.message_id)
    s = accounts.sess_get(key) or {}
    task = _tasks.pop(key, None)
    if task:
        task.cancel()
    await _drop_alerts(callback.bot, callback.message.chat.id, s)
    accounts.sess_set(key, {})
    await callback.answer("Закончили 👍")
    try:
        await callback.message.delete()
    except Exception:  # noqa: BLE001 — старше 48 ч удалить нельзя — тогда просто убираем кнопки
        await callback.message.edit_reply_markup()


# ---------- таймер ----------

@router.callback_query(F.data.startswith("ck:t:"))
async def cook_timer(callback: CallbackQuery):
    s = await _load(callback)
    if not s:
        return
    try:
        _, _, mn, mx = callback.data.split(":")
        mn, mx = int(mn), int(mx)
    except ValueError:
        await callback.answer()
        return
    now = time.time()
    s["timer"] = {"step": s["i"], "end": now + mn * 60, "end_max": now + mx * 60, "mn": mn, "mx": mx, "fired": 0}
    key = _key(callback.message.chat.id, callback.message.message_id)
    _start_task(callback.bot, callback.message.chat.id, key, s)
    await callback.answer(f"⏱ Таймер запущен: сигнал через {tm.fmt_min(mn)}")
    await _show(callback, s)


@router.callback_query(F.data == "ck:ts")
async def cook_timer_stop(callback: CallbackQuery):
    s = await _load(callback)
    if not s:
        return
    key = _key(callback.message.chat.id, callback.message.message_id)
    task = _tasks.pop(key, None)
    if task:
        task.cancel()
    s.pop("timer", None)
    await callback.answer("Таймер остановлен")
    await _show(callback, s)


def _start_task(bot: Bot, chat_id: int, key: str, s: dict):
    old = _tasks.pop(key, None)
    if old:
        old.cancel()
    task = asyncio.create_task(_run_timer(bot, chat_id, key))
    _tasks[key] = task
    task.add_done_callback(lambda t, k=key: _tasks.pop(k, None) if _tasks.get(k) is t else None)


async def _run_timer(bot: Bot, chat_id: int, key: str):
    """Ждём до сигнала (и до максимума, если «5–6 мин»), каждый раз перечитывая сессию: её могли остановить."""
    try:
        while True:
            s = accounts.sess_get(key) or {}
            t = s.get("timer")
            if not t:
                return
            targets = [t["end"]] + ([t["end_max"]] if t.get("end_max", 0) > t["end"] else [])
            if t.get("fired", 0) >= len(targets):
                return
            wait = targets[t["fired"]] - time.time()
            if wait > 0:
                await _timer_sleep(wait)
                continue
            n = t["step"] + 1
            if len(targets) == 2 and t["fired"] == 0:
                text = f"⏰ Шаг {n}: прошло {tm.fmt_min(t['mn'])} — можно проверять (максимум {tm.fmt_min(t['mx'])})."
            else:
                text = f"⏰ Шаг {n}: время вышло ({tm.fmt_min(t['mx'])})!"
            msg = await bot.send_message(chat_id, text, parse_mode=None)
            s = accounts.sess_get(key) or {}
            if not s.get("timer"):
                return
            s["timer"]["fired"] = t["fired"] + 1
            s.setdefault("alerts", []).append(msg.message_id)
            if s["timer"]["fired"] >= len(targets):
                s.pop("timer")
            accounts.sess_set(key, s)
            try:      # убрать «⏹ Остановить таймер», когда всё отзвенело
                text2, markup = render(s)
                await bot.edit_message_text(text2, chat_id=chat_id, message_id=int(key.split(":")[2]),
                                            reply_markup=markup, parse_mode=None)
            except Exception:  # noqa: BLE001
                pass
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — бот заблокирован и т.п.
        logger.warning("Таймер готовки %s не сработал", key, exc_info=True)


def resume_timers(bot: Bot) -> int:
    """После перезапуска бота: снова запустить таймеры, которые ещё не отзвенели."""
    n = 0
    for key, s in accounts.sess_items("ck:"):
        if s.get("timer"):
            chat_id = int(key.split(":")[1])
            _start_task(bot, chat_id, key, s)
            n += 1
    return n


def start_kb_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="👨‍🍳 Готовить по шагам", callback_data="ck:start")
