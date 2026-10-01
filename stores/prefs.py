"""
Какие магазины пользователь хочет видеть в списках покупок (хранится в store_prefs.json).
"""
import json
import logging
import threading
from pathlib import Path

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


def get_enabled(chat_id: int | None) -> list[str]:
    """Включённые магазины в порядке STORES. Если ничего не сохранено — все."""
    if chat_id is None:
        return list(DEFAULT_STORES)
    with _lock:
        saved = _read().get(str(chat_id))
    if not saved:
        return list(DEFAULT_STORES)
    return [s for s in STORES if s in saved] or list(DEFAULT_STORES)


def toggle(chat_id: int, store: str) -> list[str]:
    """Включает/выключает магазин; хотя бы один всегда остаётся включённым."""
    if store not in STORES:
        return get_enabled(chat_id)
    current = set(get_enabled(chat_id))
    if store in current:
        if len(current) > 1:
            current.remove(store)
    else:
        current.add(store)
    with _lock:
        data = _read()
        data[str(chat_id)] = [s for s in STORES if s in current]
        with open(PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return get_enabled(chat_id)
