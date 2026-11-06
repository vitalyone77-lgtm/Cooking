"""
Ночная копия баз (accounts.db, usage.db, web.db) в /root/backups/cooking: хранится 14 дней. Запускается из cron на сервере.
Копия делается штатным способом sqlite, база при этом может работать. Если что-то не получилось, владельцу приходит сообщение в Telegram.
"""
import datetime
import shutil
import sqlite3
import sys
from pathlib import Path

import feedback

ROOT = Path(__file__).resolve().parent
DEST = Path("/root/backups/cooking")
KEEP_DAYS = 14
SOURCES = [ROOT / "accounts.db", ROOT / "usage.db", ROOT / "web_data" / "web.db"]


def copy_db(src: Path, dst: Path) -> None:
    """Копия и проверка целостности. RuntimeError, если копия повреждена."""
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as a, sqlite3.connect(dst) as b:
        a.backup(b)
        res = b.execute("PRAGMA integrity_check").fetchone()[0]
    if res != "ok":
        raise RuntimeError(f"{src.name}: {res}")


def prune(dest: Path, keep_days: int, today: datetime.date) -> list[str]:
    """Удаляет папки копий старше срока. Трогает только папки вида ГГГГ-ММ-ДД."""
    removed = []
    for d in dest.iterdir():
        try:
            day = datetime.date.fromisoformat(d.name)
        except ValueError:
            continue
        if d.is_dir() and (today - day).days > keep_days:
            shutil.rmtree(d)
            removed.append(d.name)
    return removed


def run(dest: Path = DEST, today: datetime.date | None = None) -> Path:
    today = today or datetime.date.today()
    folder = dest / today.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    dest.chmod(0o700)
    for src in SOURCES:
        copy_db(src, folder / src.name)
    prune(dest, KEEP_DAYS, today)
    return folder


if __name__ == "__main__":
    try:
        print("копия:", run())
    except Exception as e:  # noqa: BLE001 — любой сбой копии важен владельцу
        feedback.notify_sync(f"⚠️ Ночная копия базы не сделана: {type(e).__name__}: {e}")
        sys.exit(1)
