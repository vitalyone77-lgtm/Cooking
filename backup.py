"""
Ночная копия данных в /root/backups/cooking: хранится 14 дней. Запускается из cron на сервере.

Базы (accounts, usage, web) копируются штатным способом sqlite — при этом они могут работать — и проверяются на целостность.
Файлы (анкеты и напоминания user_data.json, магазины store_prefs.json, старые меню и избранное) и загруженные картинки
рекламы копируются как есть. Пути берутся из самих модулей, чтобы копия не отставала от кода.
Каждый источник копируется отдельно: сбой одного не отменяет остальные (аудит 15.11.2026). О любом сбое владельцу
приходит сообщение в Telegram. Копия лежит на сервере, а ночью ещё уезжает архивом в Google Диск (rclone, remote gdrive, папка
Cooking-backups, хранится 14 дней); если rclone не настроен, этот шаг молча пропускается.
"""
import datetime
import os
import shutil
import subprocess
import tarfile
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = Path("/root/backups/cooking")
KEEP_DAYS = 14


def sources() -> tuple[list[Path], list[Path], list[Path]]:
    """(базы sqlite, файлы, папки) — из модулей, а не жёстко заданными путями."""
    import accounts
    import usage
    import web_db
    import storage
    import favorites
    import day_menu
    import week_menu
    from stores import prefs
    dbs = [Path(accounts.DB_PATH), Path(usage.DB_PATH), Path(web_db.DB_PATH)]
    files = [Path(storage.STORAGE_FILE), Path(prefs.PREFS_FILE), Path(favorites.BOT_FAVORITES_FILE),
             Path(day_menu.DAY_MENU_FILE), Path(week_menu.WEEK_MENU_FILE)]
    dirs = [Path(web_db.DATA_DIR) / "uploads"]
    return dbs, files, dirs


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


def run(dest: Path = DEST, today: datetime.date | None = None) -> tuple[Path, list[str]]:
    """-> (папка копии, список ошибок). Ошибка одного источника не мешает остальным."""
    today = today or datetime.date.today()
    folder = dest / today.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    dest.chmod(0o700)
    errors = []
    dbs, files, dirs = sources()
    for src in dbs:
        try:
            if not src.exists():
                raise FileNotFoundError(str(src))
            copy_db(src, folder / src.name)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{src.name}: {type(e).__name__}: {e}")
    for src in files:
        try:
            if src.exists():                       # старых файлов (меню, избранное до переезда в базу) может уже не быть
                shutil.copy2(src, folder / src.name)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{src.name}: {type(e).__name__}: {e}")
    for src in dirs:
        try:
            if src.exists():
                shutil.copytree(src, folder / src.name, dirs_exist_ok=True)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{src.name}/: {type(e).__name__}: {e}")
    try:
        prune(dest, KEEP_DAYS, today)
    except Exception as e:  # noqa: BLE001
        errors.append(f"очистка старых копий: {e}")
    return folder, errors


REMOTE = os.getenv("BACKUP_REMOTE", "gdrive:Cooking-backups")


def offsite(folder: Path, remote: str = REMOTE) -> str | None:
    """Архив папки копии в облако через rclone. -> None (успех или rclone не настроен) либо текст ошибки."""
    if not remote or not shutil.which("rclone"):
        return None
    arc = folder.parent / (folder.name + ".tar.gz")
    try:
        with tarfile.open(arc, "w:gz") as t:
            t.add(folder, arcname=folder.name)
        for cmd in (["rclone", "copyto", str(arc), f"{remote}/{arc.name}"],
                    ["rclone", "delete", remote, "--min-age", f"{KEEP_DAYS}d", "--include", "*.tar.gz"]):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            if r.returncode:
                return f"облако: {(r.stderr or '').strip().splitlines()[-1:] or r.returncode}"
        return None
    except Exception as e:  # noqa: BLE001
        return f"облако: {type(e).__name__}: {e}"
    finally:
        arc.unlink(missing_ok=True)


if __name__ == "__main__":
    import feedback
    try:
        folder, errors = run()
        cloud = offsite(folder)
        if cloud:
            errors.append(cloud)
        print("копия:", folder, "ошибки:", errors or "нет")
        if errors:
            feedback.notify_sync("⚠️ Ночная копия сделана не полностью:\n" + "\n".join(errors[:10]))
            sys.exit(1)
    except Exception as e:  # noqa: BLE001 — любой сбой копии важен владельцу
        feedback.notify_sync(f"⚠️ Ночная копия базы не сделана: {type(e).__name__}: {e}")
        sys.exit(1)
