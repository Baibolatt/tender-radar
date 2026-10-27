"""Передаёт update_id в обработчики для защиты от дублей заказов."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Update


class UpdateContextMiddleware(BaseMiddleware):
    """Кладёт update_id в data, чтобы он дошёл до хендлера."""

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        data["update_id"] = event.update_id
        return await handler(event, data)
