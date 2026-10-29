"""Тесты методов панели: список с фильтром/поиском и смена статуса."""

import pytest

from db.database import Database
from db.repositories import BouquetRepository, OrderDraft, OrderRepository, StoreRepository


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


async def _make(db, name="Айгуль", phone="+77001234567", update_id=1):
    stores = StoreRepository(db)
    # Один магазин на все заказы: второй _make переиспользует уже созданный.
    store = await stores.get_by_code("TR")
    if store is not None:
        store_id = store["id"]
    else:
        store_id = await stores.create("TR", "Магазин", florist_chat_id=999)
    bouquet_id = await BouquetRepository(db).create(store_id, "Розы", 15000)
    order = await OrderRepository(db).create(
        OrderDraft(
            store_id=store_id, source_update_id=update_id, client_chat_id=555,
            client_name=name, phone=phone, address="Тараз", delivery_date="2026-10-30",
            bouquet_id=bouquet_id,
        ),
        florist_chat_id=999,
    )
    return store_id, order


async def test_list_orders_newest_first(db):
    store_id, first = await _make(db, "Айгуль", update_id=1)
    _, second = await _make(db, "Болат", "+77009998877", update_id=2)
    rows = await OrderRepository(db).list_orders(store_id)
    assert [r["id"] for r in rows] == [second.id, first.id]


async def test_list_orders_filter_by_status(db):
    store_id, order = await _make(db)
    await OrderRepository(db).set_status(order.id, "ready")
    assert await OrderRepository(db).list_orders(store_id, status="ready") != []
    assert await OrderRepository(db).list_orders(store_id, status="delivering") == []


async def test_list_orders_search_by_name_or_phone(db):
    store_id, _ = await _make(db, "Айгуль", "+77001234567")
    assert len(await OrderRepository(db).list_orders(store_id, search="Айг")) == 1
    assert len(await OrderRepository(db).list_orders(store_id, search="70012")) == 1
    assert await OrderRepository(db).list_orders(store_id, search="неттакого") == []


async def test_set_status_updates_and_returns_row(db):
    store_id, order = await _make(db)
    row = await OrderRepository(db).set_status(order.id, "assembling")
    assert row is not None and row["status"] == "assembling"
    assert (await OrderRepository(db).get(order.id))["status"] == "assembling"
    assert await OrderRepository(db).set_status(999999, "new") is None
