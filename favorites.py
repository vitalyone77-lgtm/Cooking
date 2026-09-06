"""
Избранные рецепты — простое JSON-хранилище (по аналогии со storage.py),
плюс поиск по названию/тексту сохранённых рецептов.
"""
import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

FAVORITES_FILE = Path(__file__).parent / "favorites.json"
_lock = threading.Lock()


def _read_all() -> dict:
    if not FAVORITES_FILE.exists():
        return {}
    try:
        with open(FAVORITES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Не удалось прочитать {FAVORITES_FILE}: {e}")
        return {}


def _write_all(data: dict) -> None:
    with open(FAVORITES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_favorite(chat_id: int, title: str, text: str, cuisine: str) -> str:
    """Сохраняет рецепт в избранное, возвращает его id."""
    with _lock:
        all_data = _read_all()
        user_favs = all_data.setdefault(str(chat_id), [])
        fav_id = str(int(time.time() * 1000))
        user_favs.append({
            "id": fav_id,
            "title": title or "Рецепт без названия",
            "text": text,
            "cuisine": cuisine,
        })
        _write_all(all_data)
        return fav_id


def get_favorites(chat_id: int) -> list[dict]:
    with _lock:
        return _read_all().get(str(chat_id), [])


def get_favorite(chat_id: int, fav_id: str) -> dict | None:
    for fav in get_favorites(chat_id):
        if fav["id"] == fav_id:
            return fav
    return None


def remove_favorite(chat_id: int, fav_id: str) -> bool:
    with _lock:
        all_data = _read_all()
        user_favs = all_data.get(str(chat_id), [])
        new_favs = [f for f in user_favs if f["id"] != fav_id]
        if len(new_favs) == len(user_favs):
            return False
        all_data[str(chat_id)] = new_favs
        _write_all(all_data)
        return True


def search_favorites(chat_id: int, query: str) -> list[dict]:
    query = query.strip().lower()
    if not query:
        return get_favorites(chat_id)
    return [
        f for f in get_favorites(chat_id)
        if query in f["title"].lower() or query in f["text"].lower()
    ]
