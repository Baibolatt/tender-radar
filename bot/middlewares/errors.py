"""Глобальный перехват ошибок: логируем и не роняем бота."""

import logging

from aiogram.types import ErrorEvent

from bot.texts import ERROR_TEXT

logger = logging.getLogger(__name__)


async def handle_error(event: ErrorEvent) -> bool:
    """Логирует ошибку и пытается вежливо ответить клиенту."""
    logger.error("Необработанная ошибка", exc_info=event.exception)

    message = getattr(event.update, "message", None)
    if message is not None:
        try:
            await message.answer(ERROR_TEXT)
        except Exception:
            logger.exception("Не удалось отправить сообщение об ошибке")

    return True
