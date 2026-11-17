"""
Какие магазины пользователь хочет видеть в списках покупок.

Хранится в базе accounts (таблица store_prefs): бот и сайт — разные процессы, и общий файл store_prefs.json терял
изменения при одновременной записи (аудит 15.11.2026). Старый файл читается, пока у человека нет записи в базе.
"""
import json
import logging
import threading
from pathlib import Path

from jsonio import write_json_atomic

from .links import STORES, DEFAULT_STORES

logger = logging.getLogger(__name__)

PREFS_FILE = Path(__file__).resolve().parent.parent / "store_prefs.json"
_lock = threading.Lock()


def _read() -> dict:
    if not PREFS_FILE.exists():
        return {}
    try:
        with open(PREFS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Не удалось прочитать {PREFS_FILE}: {e}")
        return {}


_table_ready = False


def _db():
    global _table_ready
    import accounts
    if not _table_ready:
        accounts._q("CREATE TABLE IF NOT EXISTS store_prefs(uid TEXT PRIMARY KEY, stores TEXT, ts REAL)")
        _table_ready = True
    return accounts


def _saved(chat_id) -> list[str] | None:
    acc = _db()
    row = acc._q("SELECT stores FROM store_prefs WHERE uid=?", (str(chat_id),), one=True)
    if row:
        return [x for x in (row[0] or "").split(",") if x]
    with _lock:
        return _read().get(str(chat_id))           # выбор, сделанный до переезда в базу


def get_enabled(chat_id: int | None) -> list[str]:
    """Включённые магазины в порядке STORES. Если ничего не сохранено — все."""
    if chat_id is None:
        return list(DEFAULT_STORES)
    saved = _saved(chat_id)
    if not saved:
        return list(DEFAULT_STORES)
    return [s for s in STORES if s in saved] or list(DEFAULT_STORES)


def forget(chat_id) -> None:
    """Удалить выбор человека («Удалить мои данные»)."""
    _db()._q("DELETE FROM store_prefs WHERE uid=?", (str(chat_id),))
    with _lock:
        data = _read()
        if data.pop(str(chat_id), None) is not None:
            write_json_atomic(PREFS_FILE, data)



def toggle(chat_id, store: str) -> list[str]:
    """Включает/выключает магазин; хотя бы один всегда остаётся включённым."""
    if store not in STORES:
        return get_enabled(chat_id)
    current = set(get_enabled(chat_id))
    if store in current:
        if len(current) > 1:
            current.remove(store)
    else:
        current.add(store)
    import time
    _db()._q("INSERT INTO store_prefs(uid, stores, ts) VALUES(?,?,?) ON CONFLICT(uid) DO UPDATE SET stores=excluded.stores, ts=excluded.ts",
             (str(chat_id), ",".join(s for s in STORES if s in current), time.time()))
    return get_enabled(chat_id)
