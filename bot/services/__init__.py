"""Сервисы бота: общая логика диалога и канальные адаптеры."""

from bot.services.conversation_service import ConversationService
from bot.services.whatsapp_client import WhatsAppClient

__all__ = ["ConversationService", "WhatsAppClient"]
