"""Репозитории базы данных. Цены и названия букетов берутся только из БД."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from db.database import Database


def utc_now() -> str:
    """Текущее время в UTC в формате ISO 8601."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class StoreNotFound(Exception):
    """Магазин не найден или отключён."""


class BouquetNotFound(Exception):
    """Букет не найден в каталоге магазина."""


class BouquetNotAvailable(Exception):
    """Букет временно недоступен."""


class DuplicateOrder(Exception):
    """Заказ по этому сообщению уже создан."""


class FloristNotConfigured(Exception):
    """Не задан чат флориста для уведомления."""


@dataclass(frozen=True)
class OrderDraft:
    """Данные заказа. Цена и название сюда не входят — они берутся из БД."""

    store_id: int
    source_update_id: int
    client_chat_id: int
    client_name: str
    phone: str
    address: str
    delivery_date: str
    bouquet_id: int
    delivery_time: str | None = None
    card_text: str = ""
    comment: str = ""
    recipient_name: str | None = None
    recipient_phone: str | None = None
    is_gift: bool = False
    # Ключ потока оформления: повторное нажатие не создаёт второй заказ.
    idempotency_key: str | None = None


@dataclass(frozen=True)
class Order:
    """Сохранённый заказ со снимком названия и цены."""

    id: int
    public_number: str
    store_id: int
    source_update_id: int
    client_chat_id: int
    client_name: str
    phone: str
    address: str
    delivery_date: str
    delivery_time: str | None
    bouquet_id: int
    bouquet_name: str
    price_kzt: int
    card_text: str
    comment: str
    status: str
    created_at: str
    recipient_name: str | None = None
    recipient_phone: str | None = None
    is_gift: bool = False


def _order_from_row(row) -> Order:
    return Order(
        id=row["id"],
        public_number=row["public_number"],
        store_id=row["store_id"],
        source_update_id=row["source_update_id"],
        client_chat_id=row["client_chat_id"],
        client_name=row["client_name"],
        phone=row["phone"],
        address=row["address"],
        delivery_date=row["delivery_date"],
        delivery_time=row["delivery_time"],
        bouquet_id=row["bouquet_id"],
        bouquet_name=row["bouquet_name"],
        price_kzt=row["price_kzt"],
        card_text=row["card_text"],
        comment=row["comment"],
        status=row["status"],
        created_at=row["created_at"],
        recipient_name=row["recipient_name"],
        recipient_phone=row["recipient_phone"],
        is_gift=bool(row["is_gift"]),
    )


@dataclass(frozen=True)
class OutboxMessage:
    """Сообщение очереди на отправку флористу."""

    id: int
    store_id: int
    order_id: int | None
    kind: str
    chat_id: int
    payload: dict[str, Any]
    attempts: int
    next_attempt_at: str
    created_at: str


class StoreRepository:
    """Магазины."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        code: str,
        name: str,
        florist_chat_id: int | None = None,
        daily_dialog_limit: int = 100,
        max_ai_calls_per_dialog: int = 20,
        max_response_chars: int = 900,
    ) -> int:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO stores(
                    code, name, florist_chat_id, daily_dialog_limit,
                    max_ai_calls_per_dialog, max_response_chars
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    code,
                    name,
                    florist_chat_id,
                    daily_dialog_limit,
                    max_ai_calls_per_dialog,
                    max_response_chars,
                ),
            )
            return int(cursor.lastrowid)

    async def get(self, store_id: int) -> dict[str, Any] | None:
        cursor = await self.db.connection.execute(
            "SELECT * FROM stores WHERE id = ? AND is_active = 1", (store_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def get_by_code(self, code: str) -> dict[str, Any] | None:
        cursor = await self.db.connection.execute(
            "SELECT * FROM stores WHERE code = ? AND is_active = 1", (code,)
        )
        row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def set_florist_chat_id(self, store_id: int, chat_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE stores SET florist_chat_id = ? WHERE id = ?",
                (chat_id, store_id),
            )


class BouquetRepository:
    """Каталог букетов."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        store_id: int,
        name: str,
        price_kzt: int,
        description: str = "",
        is_available: bool = True,
        photo_file_id: str | None = None,
        sort_order: int = 0,
    ) -> int:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO bouquets(
                    store_id, name, description, price_kzt, is_available,
                    photo_file_id, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    store_id,
                    name,
                    description,
                    price_kzt,
                    int(is_available),
                    photo_file_id,
                    sort_order,
                ),
            )
            return int(cursor.lastrowid)

    async def create_many(
        self, store_id: int, items: list[dict[str, Any]]
    ) -> None:
        """Добавляет несколько букетов одной транзакцией."""
        rows = [
            (
                store_id,
                item["name"],
                item.get("description", ""),
                int(item["price_kzt"]),
                int(item.get("is_available", True)),
                item.get("photo_file_id"),
                int(item.get("sort_order", 0)),
            )
            for item in items
        ]
        async with self.db.transaction() as conn:
            await conn.executemany(
                """
                INSERT INTO bouquets(
                    store_id, name, description, price_kzt, is_available,
                    photo_file_id, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    async def count(self, store_id: int) -> int:
        cursor = await self.db.connection.execute(
            "SELECT COUNT(*) AS count FROM bouquets WHERE store_id = ?",
            (store_id,),
        )
        row = await cursor.fetchone()
        return int(row["count"])

    async def list_available(self, store_id: int) -> list[dict[str, Any]]:
        cursor = await self.db.connection.execute(
            """
            SELECT id, name, description, price_kzt, photo_file_id
            FROM bouquets
            WHERE store_id = ? AND is_available = 1
            ORDER BY sort_order, id
            """,
            (store_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_available(
        self, store_id: int, bouquet_id: int
    ) -> dict[str, Any] | None:
        cursor = await self.db.connection.execute(
            """
            SELECT id, name, description, price_kzt, photo_file_id
            FROM bouquets
            WHERE id = ? AND store_id = ? AND is_available = 1
            """,
            (bouquet_id, store_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row is not None else None


class OrderRepository:
    """Заказы и атомарная постановка уведомления в outbox."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, draft: OrderDraft, florist_chat_id: int | None) -> Order:
        """Сохраняет заказ и уведомление одной транзакцией.

        Сначала фиксируется заказ, затем outbox. Данные букета всегда
        перечитываются из базы, поэтому модель не может подменить цену.
        """
        if florist_chat_id is None:
            raise FloristNotConfigured("Не задан chat_id флориста")

        async with self.db.transaction() as conn:
            store_cursor = await conn.execute(
                "SELECT code FROM stores WHERE id = ? AND is_active = 1",
                (draft.store_id,),
            )
            store = await store_cursor.fetchone()
            if store is None:
                raise StoreNotFound(f"Магазин {draft.store_id} не найден")

            duplicate_cursor = await conn.execute(
                "SELECT id FROM orders WHERE store_id = ? AND source_update_id = ?",
                (draft.store_id, draft.source_update_id),
            )
            if await duplicate_cursor.fetchone() is not None:
                raise DuplicateOrder(
                    f"Заказ по сообщению {draft.source_update_id} уже создан"
                )

            if draft.idempotency_key:
                key_cursor = await conn.execute(
                    """
                    SELECT id FROM orders
                    WHERE store_id = ? AND idempotency_key = ?
                    """,
                    (draft.store_id, draft.idempotency_key),
                )
                if await key_cursor.fetchone() is not None:
                    raise DuplicateOrder(
                        f"Заказ по потоку {draft.idempotency_key} уже создан"
                    )

            bouquet_cursor = await conn.execute(
                """
                SELECT name, price_kzt, is_available
                FROM bouquets
                WHERE id = ? AND store_id = ?
                """,
                (draft.bouquet_id, draft.store_id),
            )
            bouquet = await bouquet_cursor.fetchone()
            if bouquet is None:
                raise BouquetNotFound(f"Букет {draft.bouquet_id} не найден")
            if not bouquet["is_available"]:
                raise BouquetNotAvailable(f"Букет {draft.bouquet_id} недоступен")

            await conn.execute(
                """
                INSERT INTO order_counters(store_id, last_number)
                VALUES (?, 1)
                ON CONFLICT(store_id) DO UPDATE SET last_number = last_number + 1
                """,
                (draft.store_id,),
            )
            counter_cursor = await conn.execute(
                "SELECT last_number FROM order_counters WHERE store_id = ?",
                (draft.store_id,),
            )
            counter = await counter_cursor.fetchone()
            public_number = f"{store['code']}-{counter['last_number']:06d}"

            created_at = utc_now()
            try:
                order_cursor = await conn.execute(
                    """
                    INSERT INTO orders(
                        public_number, store_id, source_update_id, client_chat_id,
                        client_name, phone, address, delivery_date, delivery_time,
                        bouquet_id, bouquet_name, price_kzt, card_text, comment,
                        recipient_name, recipient_phone, is_gift, idempotency_key,
                        status, created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'new', ?, ?
                    )
                    """,
                    (
                        public_number,
                        draft.store_id,
                        draft.source_update_id,
                        draft.client_chat_id,
                        draft.client_name,
                        draft.phone,
                        draft.address,
                        draft.delivery_date,
                        draft.delivery_time,
                        draft.bouquet_id,
                        bouquet["name"],
                        bouquet["price_kzt"],
                        draft.card_text,
                        draft.comment,
                        draft.recipient_name,
                        draft.recipient_phone,
                        int(draft.is_gift),
                        draft.idempotency_key,
                        created_at,
                        created_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                # Гонка двух одинаковых подтверждений: второй заказ не нужен.
                raise DuplicateOrder(str(exc)) from exc
            order_id = int(order_cursor.lastrowid)

            payload = {
                "order_id": order_id,
                "public_number": public_number,
                "client_chat_id": draft.client_chat_id,
                "client_name": draft.client_name,
                "phone": draft.phone,
                "address": draft.address,
                "delivery_date": draft.delivery_date,
                "delivery_time": draft.delivery_time,
                "bouquet_name": bouquet["name"],
                "price_kzt": bouquet["price_kzt"],
                "card_text": draft.card_text,
                "comment": draft.comment,
                "recipient_name": draft.recipient_name,
                "recipient_phone": draft.recipient_phone,
                "is_gift": draft.is_gift,
            }
            await conn.execute(
                """
                INSERT INTO outbox(
                    store_id, order_id, kind, chat_id, payload,
                    created_at, next_attempt_at
                ) VALUES (?, ?, 'order_notification', ?, ?, ?, ?)
                """,
                (
                    draft.store_id,
                    order_id,
                    florist_chat_id,
                    json.dumps(payload, ensure_ascii=False),
                    created_at,
                    created_at,
                ),
            )

            return Order(
                id=order_id,
                public_number=public_number,
                store_id=draft.store_id,
                source_update_id=draft.source_update_id,
                client_chat_id=draft.client_chat_id,
                client_name=draft.client_name,
                phone=draft.phone,
                address=draft.address,
                delivery_date=draft.delivery_date,
                delivery_time=draft.delivery_time,
                bouquet_id=draft.bouquet_id,
                bouquet_name=bouquet["name"],
                price_kzt=bouquet["price_kzt"],
                card_text=draft.card_text,
                comment=draft.comment,
                status="new",
                created_at=created_at,
                recipient_name=draft.recipient_name,
                recipient_phone=draft.recipient_phone,
                is_gift=draft.is_gift,
            )

    async def get(self, order_id: int) -> dict[str, Any] | None:
        cursor = await self.db.connection.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def get_by_source(
        self, store_id: int, source_update_id: int
    ) -> Order | None:
        """Возвращает заказ по ключу идемпотентности."""
        cursor = await self.db.connection.execute(
            """
            SELECT * FROM orders
            WHERE store_id = ? AND source_update_id = ?
            """,
            (store_id, source_update_id),
        )
        row = await cursor.fetchone()
        return _order_from_row(row) if row is not None else None

    async def get_by_idempotency_key(
        self, store_id: int, key: str
    ) -> Order | None:
        """Возвращает заказ, созданный по этому потоку оформления."""
        cursor = await self.db.connection.execute(
            """
            SELECT * FROM orders
            WHERE store_id = ? AND idempotency_key = ?
            """,
            (store_id, key),
        )
        row = await cursor.fetchone()
        return _order_from_row(row) if row is not None else None


class OutboxRepository:
    """Очередь уведомлений флористу."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def list_pending(
        self, limit: int = 20, now: str | None = None
    ) -> list[OutboxMessage]:
        current_time = now or utc_now()
        cursor = await self.db.connection.execute(
            """
            SELECT * FROM outbox
            WHERE status = 'pending' AND next_attempt_at <= ?
            ORDER BY id
            LIMIT ?
            """,
            (current_time, limit),
        )
        rows = await cursor.fetchall()
        return [
            OutboxMessage(
                id=row["id"],
                store_id=row["store_id"],
                order_id=row["order_id"],
                kind=row["kind"],
                chat_id=row["chat_id"],
                payload=json.loads(row["payload"]),
                attempts=row["attempts"],
                next_attempt_at=row["next_attempt_at"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def mark_sent(
        self, message_id: int, order_id: int | None = None
    ) -> None:
        """Отмечает уведомление отправленным и переводит заказ в notified."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                UPDATE outbox
                SET status = 'sent', sent_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (utc_now(), message_id),
            )
            if order_id is not None:
                await conn.execute(
                    """
                    UPDATE orders
                    SET status = 'notified', updated_at = ?
                    WHERE id = ? AND status = 'new'
                    """,
                    (utc_now(), order_id),
                )

    async def enqueue_handoff(
        self, store_id: int, chat_id: int, payload: dict[str, Any]
    ) -> int:
        """Ставит в очередь уведомление «нужен живой человек»."""
        now = utc_now()
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO outbox(
                    store_id, order_id, kind, chat_id, payload,
                    created_at, next_attempt_at
                ) VALUES (?, NULL, 'handoff', ?, ?, ?, ?)
                """,
                (
                    store_id,
                    chat_id,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    async def mark_failed(
        self,
        message_id: int,
        error: str,
        delay_seconds: int = 60,
        max_attempts: int = 5,
    ) -> None:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "SELECT attempts FROM outbox WHERE id = ? AND status = 'pending'",
                (message_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return

            attempts = int(row["attempts"]) + 1
            status = "failed" if attempts >= max_attempts else "pending"
            next_attempt_at = utc_now()
            if status == "pending":
                next_attempt_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
                ).isoformat(timespec="milliseconds").replace("+00:00", "Z")

            await conn.execute(
                """
                UPDATE outbox
                SET status = ?, attempts = ?, last_error = ?, next_attempt_at = ?
                WHERE id = ?
                """,
                (status, attempts, error[:1000], next_attempt_at, message_id),
            )


class UsageRepository:
    """Расход лимитов по магазину за день."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def add_dialog(self, store_id: int, date: str) -> None:
        await self._increment(store_id, date, "dialogs")

    async def add_ai_call(self, store_id: int, date: str) -> None:
        await self._increment(store_id, date, "ai_calls")

    async def add_tokens(
        self, store_id: int, date: str, prompt_tokens: int, completion_tokens: int
    ) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO usage_daily(store_id, date, prompt_tokens, completion_tokens)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(store_id, date) DO UPDATE SET
                    prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                    completion_tokens = completion_tokens + excluded.completion_tokens
                """,
                (store_id, date, prompt_tokens, completion_tokens),
            )

    async def get(self, store_id: int, date: str) -> dict[str, Any] | None:
        cursor = await self.db.connection.execute(
            "SELECT * FROM usage_daily WHERE store_id = ? AND date = ?",
            (store_id, date),
        )
        row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def _increment(self, store_id: int, date: str, column: str) -> None:
        if column not in {"dialogs", "ai_calls"}:
            raise ValueError(f"Неизвестный счётчик: {column}")
        async with self.db.transaction() as conn:
            await conn.execute(
                f"""
                INSERT INTO usage_daily(store_id, date, {column})
                VALUES (?, ?, 1)
                ON CONFLICT(store_id, date) DO UPDATE SET
                    {column} = {column} + 1
                """,
                (store_id, date),
            )


class DialogSessionRepository:
    """Состояние диалога: история, черновик заказа и счётчики."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def ensure(self, store_id: int, chat_id: int) -> dict[str, Any]:
        """Создаёт сессию, если её ещё нет, и возвращает её."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO dialog_sessions(store_id, chat_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(store_id, chat_id) DO NOTHING
                """,
                (store_id, chat_id, utc_now()),
            )
            cursor = await conn.execute(
                """
                SELECT * FROM dialog_sessions
                WHERE store_id = ? AND chat_id = ?
                """,
                (store_id, chat_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("Не удалось создать сессию диалога")
            return dict(row)

    async def get(self, store_id: int, chat_id: int) -> dict[str, Any] | None:
        cursor = await self.db.connection.execute(
            """
            SELECT * FROM dialog_sessions
            WHERE store_id = ? AND chat_id = ?
            """,
            (store_id, chat_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def mark_counted(self, store_id: int, chat_id: int, date: str) -> bool:
        """Считает первый диалог клиента за день и сбрасывает счётчик AI."""
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT counted_date FROM dialog_sessions
                WHERE store_id = ? AND chat_id = ?
                """,
                (store_id, chat_id),
            )
            row = await cursor.fetchone()
            if row is None or row["counted_date"] == date:
                return False

            await conn.execute(
                """
                UPDATE dialog_sessions
                SET counted_date = ?, ai_calls = 0, updated_at = ?
                WHERE store_id = ? AND chat_id = ?
                """,
                (date, utc_now(), store_id, chat_id),
            )
            await conn.execute(
                """
                INSERT INTO usage_daily(store_id, date, dialogs)
                VALUES (?, ?, 1)
                ON CONFLICT(store_id, date) DO UPDATE SET
                    dialogs = dialogs + 1
                """,
                (store_id, date),
            )
            return True

    async def add_ai_call(self, store_id: int, chat_id: int, date: str) -> None:
        """Увеличивает счётчики вызовов модели на диалог и на день."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                UPDATE dialog_sessions
                SET ai_calls = ai_calls + 1, updated_at = ?
                WHERE store_id = ? AND chat_id = ?
                """,
                (utc_now(), store_id, chat_id),
            )
            await conn.execute(
                """
                INSERT INTO usage_daily(store_id, date, ai_calls)
                VALUES (?, ?, 1)
                ON CONFLICT(store_id, date) DO UPDATE SET
                    ai_calls = ai_calls + 1
                """,
                (store_id, date),
            )

    async def save_conversation(
        self,
        store_id: int,
        chat_id: int,
        state: str,
        draft: dict[str, Any],
        history: list[dict[str, str]],
    ) -> None:
        """Сохраняет состояние, черновик и историю диалога."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                UPDATE dialog_sessions
                SET state = ?, draft_json = ?, history_json = ?, updated_at = ?
                WHERE store_id = ? AND chat_id = ?
                """,
                (
                    state,
                    json.dumps(draft, ensure_ascii=False),
                    json.dumps(history, ensure_ascii=False),
                    utc_now(),
                    store_id,
                    chat_id,
                ),
            )

    async def set_handoff_until(
        self, store_id: int, chat_id: int, until: str | None
    ) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                """
                UPDATE dialog_sessions
                SET handoff_until = ?, updated_at = ?
                WHERE store_id = ? AND chat_id = ?
                """,
                (until, utc_now(), store_id, chat_id),
            )
