"""Telegram-адаптер над ConversationService.

Вся логика диалога живёт в ConversationService. Здесь только приведение
числовых Telegram id к строкам и последовательная обработка одного чата.
"""

import asyncio
from typing import Any

from ai.client import AIClient
from bot.services.conversation_service import (
    ConversationResult,
    ConversationService,
)
from bot.services.order_service import OrderService
from config import Settings
from db.database import Database

# Прежнее имя результата оставлено для хендлеров и тестов.
DialogOutcome = ConversationResult


class DialogService:
    """Тонкий адаптер Telegram: делегирует общий сервис диалога."""

    def __init__(
        self,
        db: Database,
        settings: Settings,
        ai_client: AIClient,
        store: dict[str, Any],
        order_service: OrderService | None = None,
    ) -> None:
        self.conversation = ConversationService(
            db, settings, ai_client, store, order_service
        )
        self._locks: dict[int, asyncio.Lock] = {}

    async def reply(
        self, chat_id: int, text: str, source_update_id: int = 0
    ) -> DialogOutcome:
        """Обрабатывает сообщение. Один чат обрабатывается последовательно."""
        async with self._lock_for(chat_id):
            return await self.conversation.process_message(
                str(chat_id), text, "telegram", str(source_update_id)
            )

    async def handle_callback(
        self, chat_id: int, data: str, source_update_id: int = 0
    ) -> DialogOutcome:
        """Обрабатывает inline-кнопку."""
        async with self._lock_for(chat_id):
            return await self.conversation.process_callback(
                str(chat_id), data, "telegram", str(source_update_id)
            )

    def _lock_for(self, chat_id: int) -> asyncio.Lock:
        lock = self._locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[chat_id] = lock
        return lock
