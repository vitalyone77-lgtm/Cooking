"""
Надёжная запись JSON-файлов: сначала во временный файл рядом, потом атомарная замена.
Если процесс упадёт посреди записи (перезапуск, нехватка места), старый файл останется целым,
а не обрезанным наполовину — иначе пропали бы все избранные/меню пользователей.
"""
import json
import os
import tempfile
from pathlib import Path


def write_json_atomic(path: Path | str, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
