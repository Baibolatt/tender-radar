"""Обработчики сообщений и кнопок клиентов."""

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.services.catalog_service import Keyboard
from bot.services.dialog_service import DialogOutcome, DialogService
from bot.texts import START_TEXT

logger = logging.getLogger(__name__)


def message_chunks(text: str, limit: int = 4000) -> list[str]:
    """Делит длинный текст на части, чтобы Telegram его принял."""
    text = text or ""
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    return chunks or [""]


def build_markup(keyboard: Keyboard) -> InlineKeyboardMarkup | None:
    """Превращает простое описание кнопок в разметку Telegram."""
    if not keyboard:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=text, callback_data=data)
                for text, data in row
            ]
            for row in keyboard
        ]
    )


async def send_outcome(
    bot: Bot, chat_id: int, outcome: DialogOutcome
) -> None:
    """Отправляет ответ бота: с фото, если оно есть у букета."""
    markup = build_markup(outcome.keyboard)

    if outcome.photo_file_id:
        try:
            if len(outcome.reply) <= 1000:
                await bot.send_photo(
                    chat_id,
                    outcome.photo_file_id,
                    caption=outcome.reply,
                    reply_markup=markup,
                )
                return
            await bot.send_photo(chat_id, outcome.photo_file_id)
        except TelegramBadRequest:
            logger.warning(
                "Не удалось отправить фото букета, отправляю текстом"
            )

    chunks = message_chunks(outcome.reply)
    for chunk in chunks[:-1]:
        await bot.send_message(chat_id, chunk)
    await bot.send_message(
        chat_id, chunks[-1], reply_markup=markup
    )


async def handle_start(message: Message) -> None:
    """Приветствие по команде /start."""
    if message.bot is None:
        return
    keyboard = ((("Подобрать букет", "catalog:0"),),)
    await message.answer(START_TEXT, reply_markup=build_markup(keyboard))


async def handle_text(
    message: Message, dialog_service: DialogService, update_id: int
) -> None:
    """Передаёт текст клиента в сервис диалога."""
    if not message.text or message.bot is None:
        return

    outcome = await dialog_service.reply(
        chat_id=message.chat.id,
        text=message.text,
        source_update_id=update_id,
    )
    await send_outcome(message.bot, message.chat.id, outcome)


async def handle_callback(
    query: CallbackQuery, dialog_service: DialogService, update_id: int
) -> None:
    """Обрабатывает нажатие inline-кнопки."""
    if query.message is None or query.bot is None:
        await query.answer()
        return

    outcome = await dialog_service.handle_callback(
        chat_id=query.message.chat.id,
        data=query.data or "",
        source_update_id=update_id,
    )
    await query.answer()
    await send_outcome(query.bot, query.message.chat.id, outcome)


def build_client_router() -> Router:
    """Создаёт новый роутер, чтобы тесты не накапливали middleware."""
    router = Router(name="client")
    router.message.register(handle_start, CommandStart())
    router.message.register(handle_text, F.text)
    router.callback_query.register(handle_callback)
    return router
