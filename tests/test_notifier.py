"""Тесты воркера уведомлений: доставка, повторы и лимит попыток."""

import pytest

from bot.services.notifier import Notifier
from db.database import Database
from db.repositories import (
    BouquetRepository,
    OrderDraft,
    OrderRepository,
    OutboxMessage,
    OutboxRepository,
    StoreRepository,
)


class FakeBot:
    def __init__(self, error=None):
        self.error = error
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        if self.error is not None:
            raise self.error
        self.messages.append((chat_id, text, kwargs))


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


async def _make_order(db):
    stores = StoreRepository(db)
    bouquets = BouquetRepository(db)
    orders = OrderRepository(db)

    store_id = await stores.create("TR", "Тестовый магазин", florist_chat_id=999)
    bouquet_id = await bouquets.create(store_id, "Классические розы", 15000)
    draft = OrderDraft(
        store_id=store_id,
        source_update_id=1,
        client_chat_id=555,
        client_name="Айгуль",
        phone="+77001234567",
        address="Тараз, ул. Абая, 10",
        delivery_date="2026-10-30",
        bouquet_id=bouquet_id,
    )
    return await orders.create(draft, florist_chat_id=999)


async def test_sends_order_notification_keeps_status_new(db):
    order = await _make_order(db)
    bot = FakeBot()
    notifier = Notifier(db, bot, base_delay=0)

    sent, failed = await notifier.send_pending()

    assert (sent, failed) == (1, 0)
    assert len(bot.messages) == 1
    chat_id, text, kwargs = bot.messages[0]
    assert chat_id == 999
    assert "Новый заказ TR-000001" in text
    assert "Айгуль" in text
    assert "«Классические розы» — 15 000 ₸" in text
    assert kwargs["parse_mode"] is None

    assert await OutboxRepository(db).list_pending() == []
    order_row = await OrderRepository(db).get(order.id)
    assert order_row["status"] == "new"


async def test_failed_delivery_is_retried(db):
    await _make_order(db)
    bot = FakeBot(error=RuntimeError("network"))
    notifier = Notifier(db, bot, base_delay=0, max_attempts=3)

    sent, failed = await notifier.send_pending()
    assert (sent, failed) == (0, 1)

    pending = await OutboxRepository(db).list_pending()
    assert len(pending) == 1
    assert pending[0].attempts == 1

    bot.error = None
    sent, failed = await notifier.send_pending()
    assert (sent, failed) == (1, 0)
    assert len(bot.messages) == 1


async def test_message_becomes_failed_after_max_attempts(db):
    await _make_order(db)
    bot = FakeBot(error=RuntimeError("network"))
    notifier = Notifier(db, bot, base_delay=0, max_attempts=2)

    await notifier.send_pending()
    await notifier.send_pending()

    assert await OutboxRepository(db).list_pending() == []
    cursor = await db.connection.execute(
        "SELECT status, attempts FROM outbox"
    )
    row = await cursor.fetchone()
    assert row["status"] == "failed"
    assert row["attempts"] == 2


def test_format_handoff_message():
    message = OutboxMessage(
        id=1,
        store_id=1,
        order_id=None,
        kind="handoff",
        chat_id=999,
        payload={
            "client_chat_id": 555,
            "client_name": "Айгуль",
            "reason": "Модель недоступна",
            "last_message": "Позовите человека",
        },
        attempts=0,
        next_attempt_at="",
        created_at="",
    )

    text = Notifier.format(message)

    assert "менеджера" in text
    assert "Chat ID клиента: 555" in text
    assert "Клиент: Айгуль" in text
    assert "Причина: Модель недоступна" in text
    assert "Позовите человека" in text
    assert "Chat ID клиента: 999" not in text
