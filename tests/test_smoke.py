"""Базовые проверки каркаса: настройки и сборка диспетчера."""

from aiogram import Dispatcher

from bot.main import create_bot, create_dispatcher
from config import Settings


def test_settings_defaults(monkeypatch):
    """По умолчанию используется DeepSeek и безопасные лимиты."""
    monkeypatch.setenv("BOT_TOKEN", "123:test")
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.delenv("AI_MODEL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.ai_model == "deepseek-flash"
    assert settings.ai_max_retries == 2
    assert settings.max_response_chars == 900
    assert "test-key" not in repr(settings.ai_api_key)


def test_create_bot_requires_token(monkeypatch):
    """Без токена бот не создаётся и падает с понятной ошибкой."""
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    settings = Settings(_env_file=None)

    try:
        create_bot(settings)
    except RuntimeError as exc:
        assert "BOT_TOKEN" in str(exc)
    else:
        raise AssertionError("Ожидалась ошибка из-за отсутствия BOT_TOKEN")


def test_dispatcher_contains_router():
    """Диспетчер собирается и содержит подключённый роутер."""
    dispatcher = create_dispatcher()

    assert isinstance(dispatcher, Dispatcher)
    assert len(dispatcher.sub_routers) == 1
