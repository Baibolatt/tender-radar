"""Строгая схема ответа модели. Модель не может влиять на цену или наличие."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrderFields(BaseModel):
    """Поля заказа, которые модель извлекает из диалога."""

    model_config = ConfigDict(extra="ignore")

    client_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    address: str | None = Field(default=None, max_length=500)
    # Модель возвращает дату так, как написал клиент. Код сам переводит её.
    delivery_date: str | None = Field(default=None, max_length=50)
    delivery_time: str | None = Field(default=None, max_length=100)
    bouquet_id: int | None = None
    card_text: str | None = Field(default=None, max_length=1000)
    # Пожелания по оформлению. Текст открытки хранится отдельно в card_text.
    comment: str | None = Field(default=None, max_length=1000)
    recipient_name: str | None = Field(default=None, max_length=200)
    recipient_phone: str | None = Field(default=None, max_length=50)
    is_gift: bool | None = None
    occasion: str | None = Field(default=None, max_length=100)
    budget_kzt: int | None = Field(default=None, ge=0, le=1_000_000)

    def cleaned(self) -> dict[str, Any]:
        """Убирает пустые строки и пробелы по краям."""
        result: dict[str, Any] = {}
        for key, value in self.model_dump(exclude_none=True).items():
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    continue
            result[key] = value
        return result


class AIResponse(BaseModel):
    """Единый формат ответа модели."""

    model_config = ConfigDict(extra="ignore")

    reply: str = Field(min_length=1, max_length=4000)
    handoff: bool = False
    ready: bool = False
    order: OrderFields | None = None

    @field_validator("reply")
    @classmethod
    def reply_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reply не может быть пустым")
        return value
