"""Фоновый воркер outbox: доставляет уведомления флористу."""

import asyncio
import logging
from typing import Any, Protocol

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

from bot.services.catalog_service import format_price
from bot.services.datetime_parser import format_date_ru
from db.database import Database
from db.repositories import OutboxMessage, OutboxRepository

logger = logging.getLogger(__name__)


class MessageSender(Protocol):
    """Минимальный интерфейс бота, нужный воркеру."""

    async def send_message(
        self, chat_id: int, text: str, **kwargs: Any
    ) -> Any: ...


class Notifier:
    """Отправляет pending-сообщения и повторяет неудачные попытки."""

    def __init__(
        self,
        db: Database,
        bot: MessageSender,
        poll_interval: float = 5.0,
        batch_size: int = 20,
        max_attempts: int = 5,
        base_delay: float = 5.0,
    ) -> None:
        self.db = db
        self.bot = bot
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.outbox = OutboxRepository(db)

    async def send_pending(self) -> tuple[int, int]:
        """Отправляет текущую пачку. Возвращает (отправлено, ошибок)."""
        sent = 0
        failed = 0

        for message in await self.outbox.list_pending(limit=self.batch_size):
            text = self.format(message)
            try:
                await self.bot.send_message(
                    chat_id=message.chat_id,
                    text=text,
                    parse_mode=None,
                )
            except TelegramRetryAfter as exc:
                failed += 1
                delay = max(1, int(getattr(exc, "retry_after", self.base_delay)))
                logger.warning(
                    "Telegram просит подождать %s с для уведомления %s",
                    delay,
                    message.id,
                )
                await self.outbox.mark_failed(
                    message.id,
                    str(exc),
                    delay_seconds=delay,
                    max_attempts=self.max_attempts,
                )
            except TelegramAPIError as exc:
                failed += 1
                logger.warning(
                    "Не удалось отправить уведомление %s: %s", message.id, exc
                )
                await self.outbox.mark_failed(
                    message.id,
                    str(exc),
                    delay_seconds=self._delay(message.attempts),
                    max_attempts=self.max_attempts,
                )
            except Exception as exc:
                failed += 1
                logger.exception(
                    "Не удалось отправить уведомление %s", message.id
                )
                await self.outbox.mark_failed(
                    message.id,
                    str(exc),
                    delay_seconds=self._delay(message.attempts),
                    max_attempts=self.max_attempts,
                )
            else:
                await self.outbox.mark_sent(message.id, message.order_id)
                sent += 1
                logger.info(
                    "Уведомление %s отправлено в чат %s",
                    message.id,
                    message.chat_id,
                )

        return sent, failed

    async def run(self, stop_event: asyncio.Event) -> None:
        """Цикл воркера. Ошибки логируются, цикл не останавливается."""
        logger.info("Воркер уведомлений запущен")
        while not stop_event.is_set():
            try:
                sent, failed = await self.send_pending()
                if sent or failed:
                    logger.info(
                        "Воркер уведомлений: отправлено=%d, ошибок=%d",
                        sent,
                        failed,
                    )
            except Exception:
                logger.exception("Ошибка воркера уведомлений")

            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=self.poll_interval
                )
            except asyncio.TimeoutError:
                pass

        logger.info("Воркер уведомлений остановлен")

    @staticmethod
    def format(message: OutboxMessage) -> str:
        """Собирает текст уведомления из payload."""
        payload = message.payload

        if message.kind == "order_notification":
            lines = [
                f"Новый заказ {payload.get('public_number', '')}",
                f"Клиент: {payload.get('client_name', '')}",
                f"Телефон: {payload.get('phone', '')}",
                f"Адрес: {payload.get('address', '')}",
                "Дата доставки: "
                + format_date_ru(payload.get("delivery_date")),
            ]
            if payload.get("delivery_time"):
                lines.append(f"Время: {payload['delivery_time']}")

            if payload.get("is_gift"):
                lines.append(
                    "Получатель: "
                    + str(payload.get("recipient_name") or "")
                    + ", "
                    + str(payload.get("recipient_phone") or "")
                )

            price = payload.get("price_kzt")
            price_text = "цена не указана"
            if price is not None:
                price_text = format_price(int(price))
            lines.append(
                f"Букет: «{payload.get('bouquet_name', '')}» — {price_text}"
            )

            if payload.get("card_text"):
                lines.append(f"Открытка: {payload['card_text']}")
            if payload.get("comment"):
                lines.append(
                    f"Пожелания по оформлению: {payload['comment']}"
                )
            return "\n".join(lines)[:4000]

        if message.kind == "handoff":
            lines = ["Клиент просит менеджера."]
            client_chat_id = payload.get("client_chat_id") or message.chat_id
            lines.append(f"Chat ID клиента: {client_chat_id}")
            if payload.get("client_name"):
                lines.append(f"Клиент: {payload['client_name']}")
            if payload.get("reason"):
                lines.append(f"Причина: {payload['reason']}")
            if payload.get("public_number"):
                lines.append(f"Заказ: {payload['public_number']}")
            if payload.get("last_message"):
                lines.append(f"Последнее сообщение: {payload['last_message']}")
            return "\n".join(lines)[:4000]

        return "Уведомление от бота"[:4000]

    def _delay(self, attempts: int) -> float:
        return min(300.0, self.base_delay * (2 ** min(attempts, 6)))
