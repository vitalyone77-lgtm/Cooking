"""
SQLite для веб-версии: устройства (без регистрации), события для статистики, рекламные баннеры.
Пользователь идентифицируется случайным кодом устройства, который браузер хранит у себя.
"""
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import config

DATA_DIR = Path(__file__).parent / config.WEB_DATA_DIR
DATA_DIR.mkdir(exist_ok=True)
(DATA_DIR / "uploads").mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "web.db"
CHAT_ID_OFFSET = 10**12   # id устройств в общем ядре не пересекаются с id Telegram

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices(id INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT UNIQUE,
  created REAL, last_seen REAL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, device_id INTEGER, kind TEXT, ts REAL);
CREATE INDEX IF NOT EXISTS ev_ts ON events(ts);
CREATE INDEX IF NOT EXISTS ev_dev ON events(device_id, kind, ts);
CREATE TABLE IF NOT EXISTS ads(id INTEGER PRIMARY KEY AUTOINCREMENT, slot TEXT, title TEXT, image TEXT,
  url TEXT, active INTEGER DEFAULT 1, created REAL);
CREATE TABLE IF NOT EXISTS ad_events(id INTEGER PRIMARY KEY AUTOINCREMENT, ad_id INTEGER, kind TEXT, ts REAL);
CREATE INDEX IF NOT EXISTS ade ON ad_events(ad_id, kind);
"""


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


with db() as _c:
    _c.executescript(SCHEMA)


def create_device() -> str:
    code = uuid.uuid4().hex
    now = time.time()
    with db() as c:
        c.execute("INSERT INTO devices(uuid,created,last_seen) VALUES(?,?,?)", (code, now, now))
    return code


def device_id(code: str) -> int | None:
    """Внутренний id устройства по коду; обновляет «последний визит»."""
    if not code or len(code) != 32:
        return None
    with db() as c:
        row = c.execute("SELECT id FROM devices WHERE uuid=?", (code,)).fetchone()
        if not row:
            return None
        c.execute("UPDATE devices SET last_seen=? WHERE id=?", (time.time(), row["id"]))
        return row["id"]


def chat_id_of(dev_id: int) -> int:
    return CHAT_ID_OFFSET + dev_id


def log_event(dev_id: int, kind: str):
    with db() as c:
        c.execute("INSERT INTO events(device_id,kind,ts) VALUES(?,?,?)", (dev_id, kind, time.time()))


def count_today(dev_id: int, kind: str) -> int:
    midnight = time.time() - (time.time() % 86400)
    with db() as c:
        return c.execute("SELECT COUNT(*) n FROM events WHERE device_id=? AND kind=? AND ts>=?",
                         (dev_id, kind, midnight)).fetchone()["n"]


def stats(tg_users: int) -> dict:
    now = time.time()
    day = 86400
    with db() as c:
        one = lambda q, *a: c.execute(q, a).fetchone()[0]
        total = one("SELECT COUNT(*) FROM devices")
        out = {
            "devices_total": total,
            "new_24h": one("SELECT COUNT(*) FROM devices WHERE created>=?", now - day),
            "new_7d": one("SELECT COUNT(*) FROM devices WHERE created>=?", now - 7 * day),
            "active_24h": one("SELECT COUNT(*) FROM devices WHERE last_seen>=?", now - day),
            "active_7d": one("SELECT COUNT(*) FROM devices WHERE last_seen>=?", now - 7 * day),
            "active_30d": one("SELECT COUNT(*) FROM devices WHERE last_seen>=?", now - 30 * day),
            "tg_users": tg_users,
            "generations": {k: one("SELECT COUNT(*) FROM events WHERE kind=?", k)
                            for k in ("recipe", "daymenu", "week", "weekday")},
            "generations_24h": one("SELECT COUNT(*) FROM events WHERE kind IN ('recipe','daymenu','week','weekday') AND ts>=?", now - day),
        }
        # визиты по дням (уникальные устройства с событием 'visit'), 14 дней
        start = now - (now % day) - 13 * day
        rows = c.execute("SELECT CAST((ts-?)/? AS INT) d, COUNT(DISTINCT device_id) n FROM events "
                         "WHERE kind='visit' AND ts>=? GROUP BY d", (start, day, start)).fetchall()
        by = {r["d"]: r["n"] for r in rows}
        out["daily"] = [{"date": time.strftime("%d.%m", time.localtime(start + i * day)), "n": by.get(i, 0)}
                        for i in range(14)]
        ads = []
        for a in c.execute("SELECT * FROM ads ORDER BY id DESC").fetchall():
            v = one("SELECT COUNT(*) FROM ad_events WHERE ad_id=? AND kind='view'", a["id"])
            k = one("SELECT COUNT(*) FROM ad_events WHERE ad_id=? AND kind='click'", a["id"])
            ads.append({**dict(a), "views": v, "clicks": k})
        out["ads"] = ads
    return out


def kv_set(key: str, value: str):
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT)")
        c.execute("INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)", (key, value))


def kv_get(key: str) -> str | None:
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT)")
        r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None
