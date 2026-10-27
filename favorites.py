"""
Избранные рецепты — в общем SQLite аккаунтов (accounts.db), плюс поиск по названию/тексту.

Раньше бот и сайт хранили избранное в разных JSON-файлах; теперь у аккаунта Telegram оно одно на бот и
привязанный сайт (два процесса пишут в одну базу — JSON для этого не годится, потерялись бы записи).
Ключ: id в Telegram для бота и привязанного сайта, "w<устройство>" — для сайта без входа (accounts.web_uid).
Старые JSON (FAVORITES_FILE бота и сайта) один раз переносятся в базу при первом обращении.
"""
import json
import logging
import time
from pathlib import Path

import accounts

logger = logging.getLogger(__name__)

BOT_FAVORITES_FILE = Path(__file__).parent / "favorites.json"
FAVORITES_FILE = BOT_FAVORITES_FILE    # только для переноса старых данных (сайт подставляет свой файл)
WEB_OFFSET = 10**12      # ключи сайта в старом JSON: 10^12 + id устройства
_migrated: set[str] = set()


def _uid(chat_id) -> str:
    k = str(chat_id)
    if k.isdigit() and int(k) >= WEB_OFFSET:
        return f"w{int(k) - WEB_OFFSET}"
    return k


def _migrate() -> None:
    # файл бота переносит любой процесс, кто первым обратится (сайт показывает избранное привязанного аккаунта)
    for path in {str(BOT_FAVORITES_FILE), str(FAVORITES_FILE)}:
        if path not in _migrated:
            _migrated.add(path)
            _migrate_file(path)


def _migrate_file(path: str) -> None:
    marker = "migr:" + Path(path).resolve().as_posix()
    if accounts.sess_get(marker) or not Path(path).exists():
        return
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Не удалось прочитать %s: %s", path, e)
        return
    rows = [(_uid(k), str(f.get("id")), f.get("title") or "Рецепт без названия", f.get("text") or "",
             f.get("cuisine") or "", float(f.get("id") or 0) / 1000)
            for k, favs in data.items() for f in (favs or []) if f.get("id")]
    accounts.fav_import(rows)
    accounts.sess_set(marker, {"n": len(rows), "ts": time.time()})
    accounts.sess_mark_forever(marker)
    logger.info("Избранное: перенесено %d записей из %s", len(rows), path)


def add_favorite(chat_id, title: str, text: str, cuisine: str) -> str:
    """Сохраняет рецепт в избранное, возвращает его id."""
    _migrate()
    return accounts.fav_add(_uid(chat_id), title or "Рецепт без названия", text, cuisine)


def get_favorites(chat_id) -> list[dict]:
    _migrate()
    return accounts.fav_list(_uid(chat_id))


def get_favorite(chat_id, fav_id: str) -> dict | None:
    for fav in get_favorites(chat_id):
        if fav["id"] == fav_id:
            return fav
    return None


def remove_favorite(chat_id, fav_id: str) -> bool:
    _migrate()
    return accounts.fav_remove(_uid(chat_id), fav_id)


def search_favorites(chat_id, query: str) -> list[dict]:
    query = query.strip().lower()
    if not query:
        return get_favorites(chat_id)
    return [
        f for f in get_favorites(chat_id)
        if query in f["title"].lower() or query in f["text"].lower()
    ]
