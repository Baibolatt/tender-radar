"""aiohttp-вебхук WhatsApp Cloud API: проверка токена и приём сообщений.

Приложение собирается через фабрику и принимает зависимости снаружи, поэтому
тесты подставляют заглушки. Вебхук всегда отвечает 200 на разобранный POST:
WhatsApp иначе повторяет доставку и заваливает нас запросами.
"""

import asyncio
import hmac
import logging
import re
from typing import Any

from aiohttp import web

from bot.services.order_service import OrderService
from bot.services.whatsapp_client import (
    MAX_BUTTONS,
    MAX_BUTTON_TITLE_LENGTH,
    WhatsAppError,
)
from config import Settings

logger = logging.getLogger(__name__)

# Значения по умолчанию для зависимостей приложения.
SETTINGS = web.AppKey("whatsapp_settings", Settings)
CONVERSATION = web.AppKey("whatsapp_conversation", object)
WHATSAPP = web.AppKey("whatsapp_client", object)
LOCKS = web.AppKey("whatsapp_locks", dict)

KEYBOARD_PROMPT = "Выберите вариант:"


async def verify_webhook(request: web.Request) -> web.Response:
    """Проверка подписки WhatsApp: сверяет verify_token и отдаёт challenge."""
    settings: Settings = request.app[SETTINGS]
    token = request.query.get("hub.verify_token", "")
    challenge = request.query.get("hub.challenge", "")
    expected = settings.webhook_verify_token.get_secret_value()

    # Пустой настроенный токен не должен принимать пустой запрос.
    if expected and hmac.compare_digest(
        token.encode("utf-8"), expected.encode("utf-8")
    ):
        return web.Response(text=challenge, status=200)

    # Сам токен в лог не пишем — только факт несовпадения.
    logger.warning("WhatsApp: неверный verify_token при проверке вебхука")
    return web.Response(text="Forbidden", status=403)


async def receive_webhook(request: web.Request) -> web.Response:
    """Принимает входящие сообщения WhatsApp и отвечает клиенту."""
    try:
        payload = await request.json()
    except (ValueError, TypeError) as exc:
        # Пустое тело и битый JSON — не повод для 500 и повторных доставок.
        logger.warning(
            "WhatsApp: пустой или некорректный JSON в вебхуке (%s)",
            exc.__class__.__name__,
        )
        return web.Response(status=200)

    if not isinstance(payload, dict):
        logger.warning("WhatsApp: вебхук ожидает JSON-объект")
        return web.Response(status=200)

    for message in _extract_messages(payload):
        await _handle_message(request, message)

    return web.Response(status=200)


def _extract_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Достаёт сообщения из вложенной структуры entry/changes/value."""
    messages: list[dict[str, Any]] = []

    entries = payload.get("entry")
    if not isinstance(entries, list):
        return messages

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue

        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            batch = value.get("messages")
            if not isinstance(batch, list):
                continue

            for message in batch:
                if isinstance(message, dict):
                    messages.append(message)

    return messages


async def _handle_message(
    request: web.Request, message: dict[str, Any]
) -> None:
    """Обрабатывает одно сообщение, пропуская всё, кроме текста."""
    sender = message.get("from")
    if not isinstance(sender, str) or not sender:
        logger.warning("WhatsApp: сообщение без отправителя — пропущено")
        return

    text = message.get("text")
    body = text.get("body") if isinstance(text, dict) else None
    if not isinstance(body, str) or not body.strip():
        # Доставка, прочтение, реакция или медиа — молча пропускаем.
        return

    message_id = message.get("id")
    source_id = message_id if isinstance(message_id, str) else ""

    # Ключ пользователя — каноничный "+7...": два написания номера
    # должны попадать в одну сессию и один заказ. Для ответа оставляем
    # только цифры: Graph API не принимает ведущий "+".
    user_id = OrderService.normalize_phone(sender) or sender
    to = re.sub(r"\D", "", sender) or sender

    # Один пользователь — одна очередь: сообщения обрабатываются по порядку.
    lock = _lock_for(request.app[LOCKS], user_id)
    async with lock:
        await _process_and_reply(request, user_id, to, body, source_id)


def _lock_for(locks: dict[str, asyncio.Lock], user_id: str) -> asyncio.Lock:
    """Возвращает замок пользователя, создавая его при первом сообщении."""
    lock = locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[user_id] = lock
    return lock


async def _process_and_reply(
    request: web.Request,
    user_id: str,
    to: str,
    body: str,
    source_id: str,
) -> None:
    """Гоняет сообщение через ConversationService и отправляет ответы."""
    service = request.app[CONVERSATION]
    whatsapp = request.app[WHATSAPP]

    try:
        result = await service.process_message(
            user_id=user_id,
            text=body,
            channel="whatsapp",
            source_id=source_id,
        )
    except Exception:
        logger.exception(
            "WhatsApp: ошибка обработки сообщения от %s", user_id
        )
        return

    reply = getattr(result, "reply", "") or ""
    try:
        await whatsapp.send_text(to=to, text=reply)
    except WhatsAppError as exc:
        logger.error(
            "WhatsApp: не удалось отправить ответ %s: %s", to, exc
        )
        return
    except Exception:
        logger.exception(
            "WhatsApp: неожиданная ошибка отправки ответа %s", to
        )
        return

    buttons = _build_buttons(getattr(result, "keyboard", None))
    if not buttons:
        return

    try:
        await whatsapp.send_interactive(
            to=to, text=KEYBOARD_PROMPT, buttons=buttons
        )
    except WhatsAppError as exc:
        logger.error(
            "WhatsApp: не удалось отправить кнопки %s: %s", to, exc
        )
    except Exception:
        logger.exception(
            "WhatsApp: неожиданная ошибка отправки кнопок %s", to
        )


def _build_buttons(keyboard: Any) -> list[dict[str, str]]:
    """Превращает Keyboard в кнопки WhatsApp: не больше трёх и 20 символов."""
    buttons: list[dict[str, str]] = []
    if not isinstance(keyboard, (list, tuple)):
        return buttons

    for row in keyboard:
        if not isinstance(row, (list, tuple)):
            continue
        for button in row:
            if not isinstance(button, (list, tuple)) or len(button) < 2:
                continue

            label, callback_data = button[0], button[1]
            if not isinstance(label, str) or not isinstance(callback_data, str):
                continue

            label = label.strip()
            callback_data = callback_data.strip()
            if not label or not callback_data:
                continue

            buttons.append(
                {
                    "title": label[:MAX_BUTTON_TITLE_LENGTH],
                    "id": callback_data,
                }
            )
            if len(buttons) >= MAX_BUTTONS:
                return buttons

    return buttons


def create_whatsapp_app(
    settings: Settings, conversation_service: Any, whatsapp_client: Any
) -> web.Application:
    """Создаёт aiohttp-приложение вебхука с внедрёнными зависимостями."""
    app = web.Application()
    app[SETTINGS] = settings
    app[CONVERSATION] = conversation_service
    app[WHATSAPP] = whatsapp_client
    app[LOCKS] = {}

    app.router.add_get("/webhook", verify_webhook)
    app.router.add_post("/webhook", receive_webhook)
    return app
