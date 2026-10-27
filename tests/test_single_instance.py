"""Тесты защиты от второго экземпляра бота."""

import pytest

from bot.single_instance import AlreadyRunning, SingleInstance


def test_second_instance_is_rejected(tmp_path):
    path = tmp_path / "bot.lock"
    first = SingleInstance(path)
    first.acquire()

    try:
        with pytest.raises(AlreadyRunning):
            SingleInstance(path).acquire()
    finally:
        first.release()


def test_lock_can_be_taken_after_release(tmp_path):
    path = tmp_path / "bot.lock"

    first = SingleInstance(path).acquire()
    first.release()

    second = SingleInstance(path).acquire()
    second.release()
