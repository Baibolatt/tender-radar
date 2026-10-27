"""Подставляет сервисы в обработчики."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.services.dialog_service import DialogService


class ServicesMiddleware(BaseMiddleware):
    """Передаёт DialogService в хендлеры через data."""

    def __init__(self, dialog_service: DialogService) -> None:
        self.dialog_service = dialog_service

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["dialog_service"] = self.dialog_service
        return await handler(event, data)
