"""
Простое хранилище "последнего запроса" каждого пользователя в JSON-файле.
Нужно, чтобы напоминание в 14:00 знало, какую кухню/блюдо предлагать "как в прошлый раз",
и чтобы это переживало перезапуск бота (в отличие от FSM MemoryStorage).
"""
import json
import logging
import threading
from pathlib import Path

from jsonio import write_json_atomic

logger = logging.getLogger(__name__)

STORAGE_FILE = Path(__file__).parent / "user_data.json"
_lock = threading.Lock()
_KEEP = ("reminders_off", "reminder_recipe", "reminder_history")   # выключенная рассылка и рецепт из последнего напоминания


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
    write_json_atomic(STORAGE_FILE, data)


def save_last_request(chat_id: int, form_data: dict, dish_title: str = "") -> None:
    """Сохраняет последний успешный запрос пользователя (для напоминаний)."""
    with _lock:
        all_data = _read_all()
        old = all_data.get(str(chat_id)) or {}
        all_data[str(chat_id)] = {
            # служебные поля напоминания не теряем при новом запросе
            **{k: old[k] for k in _KEEP if k in old},
            "cuisine": form_data.get("cuisine"),
            "macro_goal": form_data.get("macro_goal", ""),
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


def _update(chat_id: int, **fields) -> None:
    with _lock:
        all_data = _read_all()
        entry = all_data.setdefault(str(chat_id), {})
        for k, v in fields.items():
            if v is None:
                entry.pop(k, None)
            else:
                entry[k] = v
        _write_all(all_data)


def set_reminders_enabled(chat_id: int, enabled: bool) -> None:
    """Выключить/включить ежедневное напоминание для пользователя."""
    _update(chat_id, reminders_off=None if enabled else True)


def reminders_enabled(chat_id: int) -> bool:
    entry = get_last_request(chat_id) or {}
    return not entry.get("reminders_off")


def save_reminder_recipe(chat_id: int, text: str, terms: list[str]) -> None:
    """Полный рецепт из напоминания: в сообщении — только название, рецепт по кнопке «Готовить»."""
    _update(chat_id, reminder_recipe={"text": text, "terms": terms})


def push_reminder_history(chat_id: int, title: str, hint: str, keep: int = 14) -> None:
    """Последние блюда из напоминаний — чтобы следующие дни не повторяли их."""
    entry = get_last_request(chat_id) or {}
    hist = (entry.get("reminder_history") or [])[-(keep - 1):] + [{"title": title, "hint": hint}]
    _update(chat_id, reminder_history=hist)


def get_reminder_recipe(chat_id: int) -> dict | None:
    return (get_last_request(chat_id) or {}).get("reminder_recipe")
