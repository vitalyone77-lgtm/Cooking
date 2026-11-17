"""Хранилище анкет бота (шаги «Подобрать», «Исключить», дневник и т. п.), которое переживает перезапуск.

Раньше было MemoryStorage: каждая выкладка обновления обрывала всем начатые анкеты (аудит 15.11.2026). Теперь
быстрая копия в памяти, а каждая запись дублируется в базу accounts (таблица fsm); после перезапуска состояние
поднимается из базы при первом обращении. Данные — pickle: в анкетах бывают не только строки и числа.
Записи старше FSM_TTL удаляются при запуске.
"""
import pickle
import time
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

import accounts

FSM_TTL = 3 * 86400


def _k(key: StorageKey) -> str:
    return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.thread_id or 0}:{key.destiny}"


def _table() -> None:
    accounts._q("CREATE TABLE IF NOT EXISTS fsm(key TEXT PRIMARY KEY, state TEXT, data BLOB, ts REAL)")


class SqliteStorage(BaseStorage):
    def __init__(self):
        _table()
        accounts._q("DELETE FROM fsm WHERE ts < ?", (time.time() - FSM_TTL,))
        self._mem: dict[str, list] = {}          # key -> [state, data]

    def _load(self, k: str) -> list:
        if k not in self._mem:
            row = accounts._q("SELECT state, data FROM fsm WHERE key=?", (k,), one=True)
            data = {}
            if row and row[1]:
                try:
                    data = pickle.loads(row[1])
                except Exception:  # noqa: BLE001 — испорченная запись: начнём анкету заново
                    data = {}
            self._mem[k] = [row[0] if row else None, data]
            if len(self._mem) > 50000:
                self._mem.clear()
                self._mem[k] = [row[0] if row else None, data]
        return self._mem[k]

    def _save(self, k: str) -> None:
        st, data = self._mem[k]
        if st is None and not data:
            accounts._q("DELETE FROM fsm WHERE key=?", (k,))
            return
        try:
            blob = pickle.dumps(data)
        except Exception:  # noqa: BLE001 — несохраняемые данные остаются только в памяти
            blob = b""
        accounts._q("INSERT INTO fsm(key, state, data, ts) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                    "state=excluded.state, data=excluded.data, ts=excluded.ts", (k, st, blob, time.time()))

    async def set_state(self, key: StorageKey, state=None) -> None:
        k = _k(key)
        rec = self._load(k)
        rec[0] = state.state if isinstance(state, State) else state
        self._save(k)

    async def get_state(self, key: StorageKey) -> str | None:
        return self._load(_k(key))[0]

    async def set_data(self, key: StorageKey, data: dict[str, Any]) -> None:
        k = _k(key)
        rec = self._load(k)
        rec[1] = dict(data)
        self._save(k)

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        return dict(self._load(_k(key))[1])

    async def close(self) -> None:
        pass
