"""
Общий учёт платных генераций (ИИ) для бота и сайта — защита от неожиданного счёта и от накрутки.

- Лимиты на АККАУНТ, а не на «сайт» или «бот» отдельно: id в Telegram для бота и для привязанного сайта
  (один счётчик на оба), "w<устройство>" — для сайта без входа. Сколько можно — решает тариф (plans.py).
- Общий потолок в сутки на весь проект (config.DAILY_GEN_CAP, по Москве), внутри него — отдельный запас
  для сайта без входа (DAILY_GEN_CAP_ANON) и лимит на один IP (WEB_IP_DAILY_GENS): накрутка новыми
  устройствами не съест запас пользователей Telegram, а подписчики получают ещё половину потолка сверху.
- «Когда пользователь последний раз заходил» — напоминания только после нескольких дней тишины (не больше двух).

Сайт и бот — разные процессы, поэтому счётчик в отдельном файле SQLite (режим WAL), который видят оба.
"""
import os
import sqlite3
import threading
import time

import config

DB_PATH = os.getenv("USAGE_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage.db"))
MSK = 3 * 3600
_lock = threading.Lock()
_ready = False

CAP_TEXT = ("😔 Сегодня помощник очень популярен, и дневной запас подборов закончился. "
            "Приходи завтра — всё снова заработает. Сохранённые меню и избранное открываются как обычно.")
ANON_CAP_TEXT = ("😔 Запас подборов для сайта без входа на сегодня закончился. Войди через Telegram "
                 "(кнопка «Профиль») — и подборы снова доступны.")
TRIAL_TEXT = "🎁 Включил тебе {days} дней полного доступа бесплатно — пользуйся всеми функциями!"


class _KeepOpen(sqlite3.Connection):
    def close(self):          # вызывающий код закрывает «по привычке» — соединение общее, не закрываем
        pass


_shared = None


def _conn():
    # WAL: чтение не ждёт записи, а запись бота и сайта (два процесса) не держит друг друга подолгу —
    # иначе ожидание блокировки замораживало бы весь бот (вызовы синхронные внутри async-обработчиков)
    global _ready
    global _shared
    if _shared is not None:
        return _shared
    # одно соединение на процесс (доступ — под _lock): открытие/закрытие на каждый запрос в режиме WAL
    # каждый раз запускает checkpoint и под нагрузкой тормозило бота в разы
    c = sqlite3.connect(DB_PATH, timeout=3, check_same_thread=False, factory=_KeepOpen)
    c.execute("PRAGMA synchronous=NORMAL")   # с WAL безопасно и без ожидания диска на каждой записи
    if not _ready:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE IF NOT EXISTS gens(id INTEGER PRIMARY KEY AUTOINCREMENT, src TEXT, uid TEXT, kind TEXT, ts REAL)")
        c.execute("CREATE INDEX IF NOT EXISTS gens_ts ON gens(ts)")
        c.execute("CREATE INDEX IF NOT EXISTS gens_uid ON gens(src, uid, kind, ts)")
        c.execute("CREATE TABLE IF NOT EXISTS seen(uid TEXT PRIMARY KEY, ts REAL)")
        if "stage" not in {r[1] for r in c.execute("PRAGMA table_info(seen)")}:
            c.execute("ALTER TABLE seen ADD COLUMN stage INTEGER DEFAULT 0")   # сколько напоминаний с последнего визита
        if "ip" not in {r[1] for r in c.execute("PRAGMA table_info(gens)")}:
            c.execute("ALTER TABLE gens ADD COLUMN ip TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS gens_acc ON gens(uid, kind, ts)")
        c.execute("CREATE INDEX IF NOT EXISTS gens_ip ON gens(ip, ts)")
        c.commit()
        _ready = True
    _shared = c
    return c


def _q(sql: str, args=(), one=False):
    with _lock:
        c = _conn()
        try:
            with c:
                cur = c.execute(sql, args)
                return cur.fetchone() if one else cur
        finally:
            c.close()


def _count(sql: str, args=()) -> int:
    return _q(sql, args, one=True)[0]


def msk_midnight(days_back: int = 0) -> float:
    now = time.time() + MSK
    return now - (now % 86400) - days_back * 86400 - MSK


def total_today() -> int:
    return _count("SELECT COUNT(*) FROM gens WHERE ts >= ?", (msk_midnight(),))


def cap_reached() -> bool:
    return config.DAILY_GEN_CAP > 0 and total_today() >= config.DAILY_GEN_CAP


def log(src: str, uid, kind: str, ip: str | None = None) -> int:
    with _lock:
        c = _conn()
        try:
            with c:
                return c.execute("INSERT INTO gens(src, uid, kind, ts, ip) VALUES (?, ?, ?, ?, ?)",
                                 (src, str(uid), kind, time.time(), ip)).lastrowid
        finally:
            c.close()


def unlog(row_id: int | None) -> None:
    """Подбор не получился (сбой ИИ) — попытку возвращаем."""
    if row_id:
        _q("DELETE FROM gens WHERE id = ?", (row_id,))


def used(uid, kind: str, days: int = 1) -> int:
    return _count("SELECT COUNT(*) FROM gens WHERE uid=? AND kind=? AND ts >= ?", (str(uid), kind, msk_midnight(days - 1)))


def _refusal(uid, kind: str, n: int, days: int) -> str:
    import plans
    anon = plans.is_anon(uid)
    if n == 0:
        what = {"daymenu": "Меню на день", "week": "План на 3 дня"}.get(kind, "Эта функция")
        return (f"{what} — после входа через Telegram (кнопка «Профиль») 🙂" if anon
                else f"{what} — в подписке 💎")
    if days == 1:
        text = "На сегодня лимит подборов исчерпан 🙂 Приходи завтра. Готовые меню и избранное открываются как обычно."
    elif kind == "week":
        text = f"План на 3 дня можно составлять раз в {days} дн. 🙂 Текущий план открывается в «Готовых меню»."
    else:
        text = f"Лимит на {days} дн. исчерпан 🙂 Скоро снова будет доступно."
    if anon and config.PAYWALL_ENABLED:
        text += " Войди через Telegram — подборов станет больше."
    elif plans.can_upsell(uid):
        text += " С подпиской 💎 — больше подборов каждый день."
    return text


def try_spend(uid, kind: str, src: str, ip: str | None = None) -> tuple[int | None, str]:
    """
    Можно ли запустить подбор. -> (id записи, уведомление или "") если можно — id потом передаётся
    в unlog() при сбое; или (None, текст отказа).
    """
    import plans
    uid = str(uid)
    premium = plans.plan_of(uid) == "premium"
    cap = config.DAILY_GEN_CAP
    if cap > 0 and total_today() >= (cap * 3 // 2 if premium else cap):
        return None, CAP_TEXT
    if plans.is_anon(uid):
        if config.DAILY_GEN_CAP_ANON > 0 and _count(
                "SELECT COUNT(*) FROM gens WHERE uid LIKE 'w%' AND ts >= ?", (msk_midnight(),)) >= config.DAILY_GEN_CAP_ANON:
            return None, ANON_CAP_TEXT
        if ip and config.WEB_IP_DAILY_GENS > 0 and _count(
                "SELECT COUNT(*) FROM gens WHERE ip=? AND ts >= ?", (ip, msk_midnight())) >= config.WEB_IP_DAILY_GENS:
            return None, "С этого адреса сегодня уже было много подборов 🙂 Войди через Telegram или приходи завтра."
    notice = ""
    lim = plans.limit(uid, kind)
    if lim:
        n, days = lim
        if used(uid, kind, days) >= n:
            until = plans.maybe_start_trial(uid)
            lim2 = plans.limit(uid, kind) if until else None
            if not lim2 or used(uid, kind, lim2[1]) >= lim2[0]:
                return None, _refusal(uid, kind, n, days)
            notice = TRIAL_TEXT.format(days=config.TRIAL_DAYS)
    return log(src, uid, kind, ip), notice


def bot_try_spend(chat_id: int, kind: str) -> tuple[int | None, str]:
    return try_spend(chat_id, kind, "bot")


def touch(chat_id) -> None:
    """Пользователь что-то нажал или написал — запоминаем время (не чаще раза в час, чтобы не писать на каждый тап)
    и обнуляем счётчик напоминаний: пока человек пользуется ботом или сайтом, напоминать не нужно."""
    now = time.time()
    if _seen_cache.get(chat_id, 0) > now - 3600:
        return
    _seen_cache[chat_id] = now
    if len(_seen_cache) > 50000:
        _seen_cache.clear()
    _q("INSERT INTO seen(uid, ts, stage) VALUES (?, ?, 0) "
       "ON CONFLICT(uid) DO UPDATE SET ts = excluded.ts, stage = 0", (str(chat_id), now))


_seen_cache: dict = {}


def last_seen(chat_id) -> float | None:
    row = _q("SELECT ts FROM seen WHERE uid = ?", (str(chat_id),), one=True)
    return row[0] if row else None


def reminder_due(chat_id: int, gaps: list[int]) -> int | None:
    """
    Пора ли напомнить. gaps — дни тишины перед каждым напоминанием, например [4, 6]: первое через 4 дня
    без визитов, второе ещё через 6, дальше молчим до следующего визита. Возвращает номер напоминания
    (1, 2, …) или None. Кого ещё не видели (появились до этого учёта) — считаем увиденными сейчас.
    """
    row = _q("SELECT ts, stage FROM seen WHERE uid = ?", (str(chat_id),), one=True)
    if row is None:
        _q("INSERT OR IGNORE INTO seen(uid, ts, stage) VALUES (?, ?, 0)", (str(chat_id), time.time()))
        return None
    ts, stage = row[0], row[1] or 0
    if stage >= len(gaps):
        return None
    if time.time() - ts >= sum(gaps[:stage + 1]) * 86400:
        return stage + 1
    return None


def mark_reminded(chat_id: int, n: int) -> None:
    """Напоминание n отправлено (сама отправка визитом не считается)."""
    _q("UPDATE seen SET stage = ? WHERE uid = ?", (n, str(chat_id)))
