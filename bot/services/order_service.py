"""Проверка черновика заказа и сохранение с уведомлением флориста."""

import html
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from bot.services.catalog_service import format_price as format_price_kzt
from bot.services.datetime_parser import format_date_ru, parse_delivery_date
from config import Settings
from db.database import Database
from db.repositories import (
    BouquetNotAvailable,
    BouquetRepository,
    DuplicateOrder,
    Order,
    OrderDraft,
    OrderRepository,
)

logger = logging.getLogger(__name__)

MAX_DELIVERY_DAYS = 60


@dataclass(frozen=True)
class ValidationResult:
    """Итог проверки черновика."""

    draft: dict[str, Any]
    missing: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing and not self.errors

    def hint(self) -> str:
        parts = []
        if self.missing:
            parts.append("не хватает: " + ", ".join(self.missing))
        if self.errors:
            parts.append("; ".join(self.errors))
        return "; ".join(parts)


class OrderValidationError(Exception):
    """Черновик нельзя превратить в заказ."""

    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        super().__init__(result.hint())


class OrderService:
    """Проверяет поля и создаёт заказ только из данных БД."""

    def __init__(
        self, db: Database, settings: Settings, store: dict[str, Any]
    ) -> None:
        self.db = db
        self.settings = settings
        self.store = store
        self.bouquets = BouquetRepository(db)
        self.orders = OrderRepository(db)

    async def validate(self, draft: dict[str, Any]) -> ValidationResult:
        """Нормализует поля и проверяет обязательные данные."""
        cleaned: dict[str, Any] = {}
        missing: list[str] = []
        errors: list[str] = []

        name = self._text(draft.get("client_name"))
        if not name:
            missing.append("имя")
        elif len(name) < 2:
            errors.append("имя указано неверно")
        else:
            cleaned["client_name"] = name

        phone_raw = self._text(draft.get("phone"))
        phone = self.normalize_phone(phone_raw)
        if not phone_raw:
            missing.append("телефон")
        elif phone is None:
            errors.append("телефон указан неверно")
        else:
            cleaned["phone"] = phone

        is_gift = bool(draft.get("is_gift"))
        cleaned["is_gift"] = is_gift
        if is_gift:
            recipient_name = self._text(draft.get("recipient_name"))
            if not recipient_name:
                missing.append("имя получателя")
            elif len(recipient_name) < 2:
                errors.append("имя получателя указано неверно")
            else:
                cleaned["recipient_name"] = recipient_name

            recipient_phone_raw = self._text(draft.get("recipient_phone"))
            recipient_phone = self.normalize_phone(recipient_phone_raw)
            if not recipient_phone_raw:
                missing.append("телефон получателя")
            elif recipient_phone is None:
                errors.append("телефон получателя указан неверно")
            else:
                cleaned["recipient_phone"] = recipient_phone

        address = self._text(draft.get("address"))
        if address:
            cleaned["address"] = address
        else:
            missing.append("адрес")

        delivery_raw = self._text(draft.get("delivery_date"))
        if not delivery_raw:
            missing.append("дата доставки")
        else:
            delivery_date = self._parse_date(delivery_raw)
            if delivery_date is None:
                errors.append("дата доставки указана неверно")
            elif delivery_date < self._today():
                errors.append("дата доставки уже прошла")
            elif delivery_date > self._today() + timedelta(days=MAX_DELIVERY_DAYS):
                errors.append("дата доставки слишком далеко")
            else:
                cleaned["delivery_date"] = delivery_date.isoformat()

        delivery_time = self._text(draft.get("delivery_time"))
        if delivery_time:
            cleaned["delivery_time"] = delivery_time
        else:
            missing.append("время доставки")

        bouquet_raw = draft.get("bouquet_id")
        if bouquet_raw is None or bouquet_raw == "":
            missing.append("букет")
        else:
            try:
                bouquet_id = int(bouquet_raw)
            except (TypeError, ValueError):
                errors.append("букет указан неверно")
            else:
                bouquet = await self.bouquets.get_available(
                    int(self.store["id"]), bouquet_id
                )
                if bouquet is None:
                    errors.append("выбранный букет недоступен")
                else:
                    cleaned["bouquet_id"] = bouquet_id

        # Текст открытки и пожелания по оформлению — разные поля.
        for field in ("card_text", "comment"):
            value = self._text(draft.get(field))
            if value:
                cleaned[field] = value

        return ValidationResult(
            draft=cleaned,
            missing=tuple(missing),
            errors=tuple(errors),
        )

    async def is_ready(self, draft: dict[str, Any]) -> bool:
        """Все обязательные поля заполнены и проходят проверку."""
        return (await self.validate(draft)).ok

    async def summary(self, draft: dict[str, Any]) -> str:
        """Сводка заказа, собранная кодом из базы."""
        result = await self.validate(draft)
        if not result.ok:
            return "Проверьте данные. " + result.hint()

        data = result.draft
        bouquet = await self.bouquets.get_available(
            int(self.store["id"]), int(data["bouquet_id"])
        )
        if bouquet is None:
            return "Выбранный букет больше недоступен. Выберите другой, пожалуйста."

        lines = [
            "Проверьте заказ:",
            f"Букет: «{html.escape(bouquet['name'])}» — "
            f"{self.format_price(bouquet['price_kzt'])}",
            f"Заказчик: {html.escape(data['client_name'])}, {data['phone']}",
        ]
        if data.get("is_gift"):
            lines.append(
                f"Получатель: {html.escape(data['recipient_name'])}, "
                f"{data['recipient_phone']}"
            )
        else:
            lines.append("Получатель: заказчик")
        lines.extend(
            [
                f"Адрес: {html.escape(data['address'])}",
                f"Дата: {format_date_ru(data['delivery_date'])}",
                f"Время: {html.escape(data['delivery_time'])}",
            ]
        )
        if data.get("card_text"):
            lines.append(f"Открытка: «{html.escape(data['card_text'])}»")
        else:
            lines.append("Открытка: без открытки")
        if data.get("comment"):
            lines.append(
                f"Пожелания по оформлению: {html.escape(data['comment'])}"
            )
        lines.append("Всё верно?")
        return "\n".join(lines)

    async def create(
        self, draft: dict[str, Any], chat_id: int, source_update_id: int
    ) -> Order:
        """Создаёт заказ и уведомление одной транзакцией."""
        result = await self.validate(draft)
        if not result.ok:
            raise OrderValidationError(result)

        florist_chat_id = self.store.get("florist_chat_id") or self.settings.florist_chat_id
        if florist_chat_id is None:
            raise OrderValidationError(
                ValidationResult(
                    draft=result.draft,
                    errors=("не настроен чат флориста",),
                )
            )

        data = result.draft
        store_id = int(self.store["id"])
        # Ключ потока: два нажатия «Всё верно» не создают два заказа.
        idempotency_key = self._text(draft.get("flow_id")) or str(
            source_update_id
        )

        existing = await self.orders.get_by_idempotency_key(
            store_id, idempotency_key
        )
        if existing is not None:
            logger.warning(
                "Повторное подтверждение заказа %s", existing.public_number
            )
            return existing

        order_draft = OrderDraft(
            store_id=store_id,
            source_update_id=source_update_id,
            client_chat_id=chat_id,
            client_name=data["client_name"],
            phone=data["phone"],
            address=data["address"],
            delivery_date=data["delivery_date"],
            delivery_time=data.get("delivery_time"),
            bouquet_id=int(data["bouquet_id"]),
            card_text=data.get("card_text", ""),
            comment=data.get("comment", ""),
            recipient_name=data.get("recipient_name"),
            recipient_phone=data.get("recipient_phone"),
            is_gift=bool(data.get("is_gift")),
            idempotency_key=idempotency_key,
        )

        try:
            return await self.orders.create(
                order_draft, florist_chat_id=int(florist_chat_id)
            )
        except DuplicateOrder:
            existing = await self.orders.get_by_idempotency_key(
                store_id, idempotency_key
            )
            if existing is None:
                existing = await self.orders.get_by_source(
                    store_id, source_update_id
                )
            if existing is None:
                raise
            logger.warning("Повторная обработка заказа %s", existing.public_number)
            return existing
        except BouquetNotAvailable:
            raise OrderValidationError(
                ValidationResult(
                    draft=result.draft,
                    errors=("выбранный букет недоступен",),
                )
            )

    def accepted_text(self, order: Order) -> str:
        lines = [
            f"Заказ оформлен! Номер: {order.public_number}.",
            f"Букет: «{html.escape(order.bouquet_name)}» — "
            f"{self.format_price(order.price_kzt)}.",
        ]
        if order.is_gift and order.recipient_name:
            lines.append(f"Получатель: {html.escape(order.recipient_name)}.")
        delivery = f"Доставка: {format_date_ru(order.delivery_date)}"
        if order.delivery_time:
            delivery += f", {html.escape(order.delivery_time)}"
        lines.append(delivery + ".")
        lines.append(self.ready_text())
        lines.append("Флорист получит заявку и свяжется с вами.")
        return "\n".join(lines)

    def ready_text(self) -> str:
        """Срок готовности только из конфига, без выдумок."""
        minutes = int(self.settings.ready_minutes)
        if minutes == 60:
            return (
                "Обычно букет готов примерно через час — "
                "флорист подтвердит точнее."
            )
        if minutes == 1:
            return "Обычно букет готов примерно через минуту — флорист подтвердит точнее."
        return (
            f"Обычно букет готов примерно через {minutes} минут — "
            "флорист подтвердит точнее."
        )

    @staticmethod
    def validation_text(result: ValidationResult) -> str:
        return (
            "Не получилось оформить заказ: "
            + result.hint()
            + ". Уточните, пожалуйста."
        )

    @staticmethod
    def normalize_phone(value: str | None) -> str | None:
        """Приводит казахстанский номер к виду +7XXXXXXXXXX.

        Принимает 8XXXXXXXXXX, 7XXXXXXXXXX и 10 цифр без кода страны.
        После +7 должно быть ровно 10 цифр, первая — 7.
        """
        digits = re.sub(r"\D", "", value or "")
        if len(digits) == 11 and digits[0] in {"7", "8"}:
            national = digits[1:]
        elif len(digits) == 10:
            national = digits
        else:
            return None
        if len(national) != 10 or national[0] != "7":
            return None
        return "+7" + national

    @staticmethod
    def format_price(price_kzt: int) -> str:
        return format_price_kzt(price_kzt)

    def _today(self) -> date:
        return datetime.now(ZoneInfo(self.settings.timezone)).date()

    def _parse_date(self, value: str) -> date | None:
        return parse_delivery_date(value, self._today())

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()
