"""Настройки приложения. Читаются только из переменных окружения и .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Все настройки бота. Секреты хранятся в SecretStr и не попадают в логи."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Секреты
    bot_token: SecretStr = Field(default=SecretStr(""), alias="BOT_TOKEN")
    ai_api_key: SecretStr = Field(default=SecretStr(""), alias="AI_API_KEY")

    # Модель (Google Gemini API)
    ai_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta",
        alias="AI_BASE_URL",
    )
    ai_model: str = Field(default="gemini-3.1-flash-lite", alias="AI_MODEL")
    ai_timeout_seconds: float = Field(default=20.0, alias="AI_TIMEOUT_SECONDS")
    ai_max_retries: int = Field(default=2, alias="AI_MAX_RETRIES")
    ai_retry_base_delay_seconds: float = Field(
        default=1.0, alias="AI_RETRY_BASE_DELAY_SECONDS"
    )
    ai_max_tokens: int = Field(default=300, alias="AI_MAX_TOKENS")

    # Магазин и база
    store_code: str = Field(default="TR", alias="STORE_CODE")
    timezone: str = Field(default="Asia/Almaty", alias="TIMEZONE")
    florist_chat_id: int | None = Field(default=None, alias="FLORIST_CHAT_ID")
    database_path: Path = Field(default=Path("data/bot.db"), alias="DATABASE_PATH")

    # Каталог и сроки
    ready_minutes: int = Field(default=60, alias="READY_MINUTES")
    catalog_page_size: int = Field(default=5, alias="CATALOG_PAGE_SIZE")

    # Ограничения длины (не блокируют диалог)
    max_response_chars: int = Field(default=900, alias="MAX_RESPONSE_CHARS")
    max_input_chars: int = Field(default=2000, alias="MAX_INPUT_CHARS")
    handoff_cooldown_minutes: int = Field(
        default=30, alias="HANDOFF_COOLDOWN_MINUTES"
    )

    # Логи
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")


@lru_cache
def get_settings() -> Settings:
    """Возвращает настройки один раз за запуск процесса."""
    return Settings()
