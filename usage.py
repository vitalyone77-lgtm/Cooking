"""
Общий учёт платных генераций (ИИ) для бота и сайта — защита от неожиданного счёта.

- Общий потолок в сутки на весь проект (config.DAILY_GEN_CAP, по Москве): после него подборы
  вежливо отклоняются до полуночи. Считаются и бот, и сайт, и ежедневные напоминания.
- Лимиты бота на пользователя — как на сайте: 3 блюда, 1 меню на день, 1 план за 3 дня, 5 замен.
- «Когда пользователь последний раз заходил» — напоминания шлём только активным.

Сайт и бот — разные процессы, поэтому счётчик в отдельном файле SQLite, который видят оба.
"""
import os
import sqlite3
import threading
import time

import config

DB_PATH = os.getenv("USAGE_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage.db"))
MSK = 3 * 3600
_lock = threading.Lock()

# вид подбора -> (сколько раз, за сколько календарных дней по Москве, как назвать)
BOT_LIMITS = {
    "recipe": (config.WEB_LIMIT_RECIPE, 1),
    "daymenu": (config.WEB_LIMIT_DAYMENU, 1),
    "week": (config.WEB_LIMIT_WEEK, 3),
    "missing": (config.WEB_LIMIT_MISSING, 1),
}

CAP_TEXT = ("😔 Сегодня помощник очень популярен, и дневной запас подборов закончился. "
            "Приходи завтра — всё снова заработает. Сохранённые меню и избранное открываются как обычно.")


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS gens(id INTEGER PRIMARY KEY AUTOINCREMENT, src TEXT, uid TEXT, kind TEXT, ts REAL)")
    c.execute("CREATE INDEX IF NOT EXISTS gens_ts ON gens(ts)")
    c.execute("CREATE INDEX IF NOT EXISTS gens_uid ON gens(src, uid, kind, ts)")
    c.execute("CREATE TABLE IF NOT EXISTS seen(uid TEXT PRIMARY KEY, ts REAL)")
    return c


def msk_midnight(days_back: int = 0) -> float:
    now = time.time() + MSK
    return now - (now % 86400) - days_back * 86400 - MSK


def total_today() -> int:
    with _lock, _conn() as c:
        return c.execute("SELECT COUNT(*) FROM gens WHERE ts >= ?", (msk_midnight(),)).fetchone()[0]


def cap_reached() -> bool:
    return config.DAILY_GEN_CAP > 0 and total_today() >= config.DAILY_GEN_CAP


def log(src: str, uid, kind: str) -> int:
    with _lock, _conn() as c:
        return c.execute("INSERT INTO gens(src, uid, kind, ts) VALUES (?, ?, ?, ?)",
                         (src, str(uid), kind, time.time())).lastrowid


def unlog(row_id: int | None) -> None:
    """Подбор не получился (сбой ИИ) — попытку возвращаем."""
    if row_id:
        with _lock, _conn() as c:
            c.execute("DELETE FROM gens WHERE id = ?", (row_id,))


def unlog_last(src: str, uid, kind: str) -> None:
    with _lock, _conn() as c:
        c.execute("DELETE FROM gens WHERE id = (SELECT MAX(id) FROM gens WHERE src=? AND uid=? AND kind=?)",
                  (src, str(uid), kind))


def bot_try_spend(chat_id: int, kind: str) -> tuple[int | None, str]:
    """
    Можно ли пользователю бота запустить подбор. Возвращает (id записи, "") если можно —
    id потом передаётся в unlog() при сбое; или (None, текст отказа).
    """
    if cap_reached():
        return None, CAP_TEXT
    if kind in BOT_LIMITS:
        n, days = BOT_LIMITS[kind]
        with _lock, _conn() as c:
            used = c.execute("SELECT COUNT(*) FROM gens WHERE src='bot' AND uid=? AND kind=? AND ts >= ?",
                             (str(chat_id), kind, msk_midnight(days - 1))).fetchone()[0]
        if used >= n:
            if days == 1:
                return None, "На сегодня лимит подборов исчерпан 🙂 Приходи завтра. Готовые меню и избранное открываются как обычно."
            return None, "План на 3 дня можно составлять раз в три дня 🙂 Текущий план открывается в «Готовых меню»."
    return log("bot", chat_id, kind), ""


def touch(chat_id: int) -> None:
    """Пользователь что-то нажал или написал — запоминаем время (не чаще раза в час, чтобы не писать на каждый тап)."""
    now = time.time()
    if _seen_cache.get(chat_id, 0) > now - 3600:
        return
    _seen_cache[chat_id] = now
    with _lock, _conn() as c:
        c.execute("INSERT INTO seen(uid, ts) VALUES (?, ?) ON CONFLICT(uid) DO UPDATE SET ts = excluded.ts",
                  (str(chat_id), now))


_seen_cache: dict[int, float] = {}


def last_seen(chat_id: int) -> float | None:
    with _lock, _conn() as c:
        row = c.execute("SELECT ts FROM seen WHERE uid = ?", (str(chat_id),)).fetchone()
    return row[0] if row else None


def is_active(chat_id: int, days: int) -> bool:
    """Заходил ли за последние days дней. Кого ещё не видели (появились до этого учёта) — считаем
    увиденными сейчас: им ещё days дней напоминаний, дальше — только если вернутся."""
    ts = last_seen(chat_id)
    if ts is None:
        _seen_cache.pop(chat_id, None)
        touch(chat_id)
        return True
    return ts >= time.time() - days * 86400
