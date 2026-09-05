"""
Простое хранилище "последнего запроса" каждого пользователя в JSON-файле.
Нужно, чтобы напоминание в 14:00 знало, какую кухню/блюдо предлагать "как в прошлый раз",
и чтобы это переживало перезапуск бота (в отличие от FSM MemoryStorage).
"""
import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

STORAGE_FILE = Path(__file__).parent / "user_data.json"
_lock = threading.Lock()


def _read_all() -> dict:
    if not STORAGE_FILE.exists():
        return {}
    try:
        with open(STORAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Не удалось прочитать {STORAGE_FILE}: {e}")
        return {}


def _write_all(data: dict) -> None:
    with open(STORAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_last_request(chat_id: int, form_data: dict, dish_title: str = "") -> None:
    """Сохраняет последний успешный запрос пользователя (для напоминаний)."""
    with _lock:
        all_data = _read_all()
        all_data[str(chat_id)] = {
            "cuisine": form_data.get("cuisine"),
            "preferred": form_data.get("preferred", ""),
            "excluded": form_data.get("excluded", ""),
            "time": form_data.get("time"),
            "servings": form_data.get("servings"),
            "appliance": form_data.get("appliance", []),
            "appliance_labels": form_data.get("appliance_labels", []),
            "dish_title": dish_title,
        }
        _write_all(all_data)


def get_last_request(chat_id: int) -> dict | None:
    with _lock:
        return _read_all().get(str(chat_id))


def get_all_users() -> dict:
    """Возвращает {chat_id: last_request_data} для всех, кто хоть раз получал рецепт."""
    with _lock:
        return _read_all()
