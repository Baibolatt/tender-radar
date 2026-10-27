"""Защита от запуска второго экземпляра бота на той же базе."""

import fcntl
import os
from pathlib import Path


class AlreadyRunning(RuntimeError):
    """Бот с такой же базой уже запущен."""


class SingleInstance:
    """Держит неблокирующую блокировку файла, пока бот работает."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._file = None

    def acquire(self) -> "SingleInstance":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(
                handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
            )
        except OSError as exc:
            handle.close()
            raise AlreadyRunning(
                "Бот уже запущен. Второй экземпляр не нужен: "
                "он создаёт дубли заказов."
            ) from exc

        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._file = handle
        return self

    def release(self) -> None:
        if self._file is None:
            return
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None

    def __enter__(self) -> "SingleInstance":
        return self.acquire()

    def __exit__(self, *args: object) -> None:
        self.release()
