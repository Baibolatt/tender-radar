"""Подключение к SQLite, миграции и безопасные транзакции."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class Database:
    """Одно соединение SQLite с WAL и блокировкой записи."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._connection: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()
        self._transaction_depth: ContextVar[int] = ContextVar(
            f"transaction_depth_{id(self)}", default=0
        )

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("База не подключена. Сначала вызовите connect().")
        return self._connection

    async def connect(self) -> None:
        """Открывает базу и включает WAL, внешние ключи и таймаут блокировок."""
        if self._connection is not None:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.path, isolation_level=None)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await self._connection.execute("PRAGMA foreign_keys=ON")
        await self._connection.execute("PRAGMA busy_timeout=5000")

    async def close(self) -> None:
        """Закрывает соединение."""
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Пишет в базу через BEGIN IMMEDIATE; вложенный вызов продолжает транзакцию."""
        if self._transaction_depth.get() > 0:
            yield self.connection
            return

        async with self._write_lock:
            token = self._transaction_depth.set(1)
            try:
                await self.connection.execute("BEGIN IMMEDIATE")
                yield self.connection
                await self.connection.commit()
            except BaseException:
                await self.connection.rollback()
                raise
            finally:
                self._transaction_depth.reset(token)

    async def migrate(self) -> None:
        """Применяет новые SQL-миграции по порядку номеров в имени файла."""
        cursor = await self.connection.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        current_version = int(row[0]) if row else 0

        for migration_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = int(migration_path.stem.split("_", 1)[0])
            if version <= current_version:
                continue

            sql = migration_path.read_text(encoding="utf-8")
            await self.connection.executescript(sql)
            await self.connection.execute(f"PRAGMA user_version = {version}")
            await self.connection.commit()
            current_version = version
