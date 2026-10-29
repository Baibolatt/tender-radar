"""Тесты базы: схема, заказ, outbox и транзакции."""

import pytest

from db.database import Database
from db.repositories import (
    BouquetNotAvailable,
    BouquetRepository,
    DuplicateOrder,
    OrderDraft,
    OrderRepository,
    OutboxRepository,
    StoreRepository,
)


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


async def _make_store_and_bouquet(db, price=15000, available=True):
    stores = StoreRepository(db)
    bouquets = BouquetRepository(db)
    store_id = await stores.create("TR", "Тестовый магазин", florist_chat_id=999)
    bouquet_id = await bouquets.create(
        store_id, "Классические розы", price, is_available=available
    )
    return store_id, bouquet_id


def _draft(store_id, bouquet_id, update_id=1, idempotency_key=None):
    return OrderDraft(
        store_id=store_id,
        source_update_id=update_id,
        client_chat_id=555,
        client_name="Айгуль",
        phone="+77001234567",
        address="Тараз, ул. Абая, 10",
        delivery_date="2026-10-30",
        bouquet_id=bouquet_id,
        delivery_time="14:00-16:00",
        card_text="С днём рождения!",
        idempotency_key=idempotency_key,
    )


async def test_migrations_create_all_tables(db):
    cursor = await db.connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )
    tables = {row["name"] for row in await cursor.fetchall()}

    assert {
        "stores",
        "bouquets",
        "orders",
        "order_counters",
        "outbox",
        "dialog_sessions",
        "usage_daily",
    } <= tables


async def test_migrations_are_idempotent(db):
    await db.migrate()
    await db.migrate()

    cursor = await db.connection.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    assert row[0] == 4


async def test_order_has_recipient_and_idempotency_columns(db):
    cursor = await db.connection.execute("PRAGMA table_info(orders)")
    columns = {row["name"] for row in await cursor.fetchall()}

    assert {
        "recipient_name",
        "recipient_phone",
        "is_gift",
        "idempotency_key",
    } <= columns


async def test_duplicate_idempotency_key_is_rejected(db):
    store_id, bouquet_id = await _make_store_and_bouquet(db)
    orders = OrderRepository(db)
    draft = _draft(
        store_id, bouquet_id, update_id=1, idempotency_key="flow-1"
    )

    await orders.create(draft, florist_chat_id=999)
    second = _draft(
        store_id, bouquet_id, update_id=2, idempotency_key="flow-1"
    )
    with pytest.raises(DuplicateOrder):
        await orders.create(second, florist_chat_id=999)

    found = await orders.get_by_idempotency_key(store_id, "flow-1")
    assert found is not None
    assert found.public_number == "TR-000001"


async def test_order_and_outbox_are_created_together(db):
    store_id, bouquet_id = await _make_store_and_bouquet(db, price=15000)
    orders = OrderRepository(db)
    outbox = OutboxRepository(db)

    order = await orders.create(_draft(store_id, bouquet_id), florist_chat_id=999)

    assert order.public_number == "TR-000001"
    assert order.price_kzt == 15000
    assert order.bouquet_name == "Классические розы"
    assert order.status == "new"

    pending = await outbox.list_pending()
    assert len(pending) == 1
    assert pending[0].order_id == order.id
    assert pending[0].kind == "order_notification"
    assert pending[0].chat_id == 999
    assert pending[0].payload["price_kzt"] == 15000
    assert pending[0].payload["public_number"] == "TR-000001"


async def test_price_is_taken_from_db(db):
    store_id, bouquet_id = await _make_store_and_bouquet(db, price=17500)
    order = await OrderRepository(db).create(
        _draft(store_id, bouquet_id), florist_chat_id=999
    )

    assert order.price_kzt == 17500


async def test_unavailable_bouquet_is_rejected(db):
    store_id, bouquet_id = await _make_store_and_bouquet(db, available=False)

    with pytest.raises(BouquetNotAvailable):
        await OrderRepository(db).create(
            _draft(store_id, bouquet_id), florist_chat_id=999
        )

    cursor = await db.connection.execute("SELECT COUNT(*) AS count FROM orders")
    assert (await cursor.fetchone())["count"] == 0


async def test_duplicate_update_id_is_rejected(db):
    store_id, bouquet_id = await _make_store_and_bouquet(db)
    orders = OrderRepository(db)
    draft = _draft(store_id, bouquet_id, update_id=42)

    await orders.create(draft, florist_chat_id=999)
    with pytest.raises(DuplicateOrder):
        await orders.create(draft, florist_chat_id=999)

    cursor = await db.connection.execute("SELECT COUNT(*) AS count FROM orders")
    assert (await cursor.fetchone())["count"] == 1


async def test_transaction_rolls_back_on_error(db):
    with pytest.raises(RuntimeError):
        async with db.transaction() as conn:
            await conn.execute(
                "INSERT INTO stores(code, name) VALUES ('XX', 'Ошибка')"
            )
            raise RuntimeError("сбой")

    cursor = await db.connection.execute(
        "SELECT COUNT(*) AS count FROM stores WHERE code = 'XX'"
    )
    assert (await cursor.fetchone())["count"] == 0


async def test_outbox_marks_sent_and_failed(db):
    store_id, bouquet_id = await _make_store_and_bouquet(db)
    orders = OrderRepository(db)
    outbox = OutboxRepository(db)

    first = await orders.create(
        _draft(store_id, bouquet_id, update_id=1), florist_chat_id=999
    )
    second = await orders.create(
        _draft(store_id, bouquet_id, update_id=2), florist_chat_id=999
    )
    pending = await outbox.list_pending()
    assert len(pending) == 2
    assert second.public_number == "TR-000002"

    await outbox.mark_sent(pending[0].id)
    await outbox.mark_failed(pending[1].id, "сеть недоступна", delay_seconds=0)
    pending_after = await outbox.list_pending()
    assert [message.id for message in pending_after] == [pending[1].id]

    cursor = await db.connection.execute(
        "SELECT status, sent_at FROM outbox WHERE id = ?", (pending[0].id,)
    )
    sent_row = await cursor.fetchone()
    assert sent_row["status"] == "sent"
    assert sent_row["sent_at"] is not None
