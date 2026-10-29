"""Тесты API панели заказов: вход, список, поиск, смена статуса."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from config import Settings
from db.database import Database
from db.repositories import BouquetRepository, OrderDraft, OrderRepository, StoreRepository
from web.app import create_app


def _settings(tmp_path):
    return Settings(
        _env_file=None,
        bot_token="123:test",
        ai_api_key="x",
        panel_password="secret",
        database_path=tmp_path / "t.db",
    )


async def _make_order(db, store_id, bouquet_id, name="Айгуль", phone="+77001234567", update_id=1):
    return await OrderRepository(db).create(
        OrderDraft(
            store_id=store_id, source_update_id=update_id, client_chat_id=555,
            client_name=name, phone=phone, address="Тараз", delivery_date="2026-10-30",
            bouquet_id=bouquet_id,
        ),
        florist_chat_id=999,
    )


@pytest.fixture
async def client_and_db(tmp_path):
    settings = _settings(tmp_path)
    db = Database(settings.database_path)
    await db.connect()
    await db.migrate()
    store_id = await StoreRepository(db).create("TR", "Магазин", florist_chat_id=999)
    bouquet_id = await BouquetRepository(db).create(store_id, "Розы", 15000)
    app = create_app(settings, db)
    async with TestClient(TestServer(app)) as client:
        yield client, db, store_id, bouquet_id
    await db.close()


async def test_login_wrong_password_returns_401(client_and_db):
    client, db, store_id, bouquet_id = client_and_db
    resp = await client.post("/login", json={"password": "nope"})
    assert resp.status == 401


async def test_login_sets_cookie_and_me_ok(client_and_db):
    client, db, store_id, bouquet_id = client_and_db
    resp = await client.post("/login", json={"password": "secret"})
    assert resp.status == 200
    me = await client.get("/api/me")
    assert me.status == 200
    data = await me.json()
    assert data["authenticated"] is True


async def test_api_requires_auth(client_and_db):
    client, db, store_id, bouquet_id = client_and_db
    resp = await client.get("/api/orders")
    assert resp.status == 401


async def test_orders_list_and_search(client_and_db):
    client, db, store_id, bouquet_id = client_and_db
    await _make_order(db, store_id, bouquet_id, "Айгуль", "+77001234567", 1)
    await _make_order(db, store_id, bouquet_id, "Болат", "+77009998877", 2)
    await client.post("/login", json={"password": "secret"})
    resp = await client.get("/api/orders", params={"q": "Айг"})
    data = await resp.json()
    assert resp.status == 200
    assert len(data["orders"]) == 1
    assert data["orders"][0]["client_name"] == "Айгуль"
    assert "store_id" not in data["orders"][0]


async def test_set_status_valid(client_and_db):
    client, db, store_id, bouquet_id = client_and_db
    order = await _make_order(db, store_id, bouquet_id)
    await client.post("/login", json={"password": "secret"})
    resp = await client.post(f"/api/orders/{order.id}/status", json={"status": "ready"})
    assert resp.status == 200
    data = await resp.json()
    assert data["order"]["status"] == "ready"


async def test_set_status_invalid_returns_400(client_and_db):
    client, db, store_id, bouquet_id = client_and_db
    order = await _make_order(db, store_id, bouquet_id)
    await client.post("/login", json={"password": "secret"})
    resp = await client.post(f"/api/orders/{order.id}/status", json={"status": "bogus"})
    assert resp.status == 400


async def test_statuses_endpoint(client_and_db):
    client, db, store_id, bouquet_id = client_and_db
    await client.post("/login", json={"password": "secret"})
    resp = await client.get("/api/statuses")
    data = await resp.json()
    assert len(data["statuses"]) == 6
    assert data["statuses"][0]["key"] == "new"
