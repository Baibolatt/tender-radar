"""Тесты API обращений флористу."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from config import Settings
from db.database import Database
from db.repositories import HandoffRepository, StoreRepository
from web.app import create_app


def _settings(tmp_path):
    return Settings(
        _env_file=None,
        bot_token="123:test",
        ai_api_key="x",
        panel_password="secret",
        database_path=tmp_path / "t.db",
    )


@pytest.fixture
async def client_and_db(tmp_path):
    settings = _settings(tmp_path)
    db = Database(settings.database_path)
    await db.connect()
    await db.migrate()
    store_id = await StoreRepository(db).create("TR", "Магазин", florist_chat_id=999)
    app = create_app(settings, db)
    async with TestClient(TestServer(app)) as client:
        yield client, db, store_id
    await db.close()


async def _login(client):
    await client.post("/login", json={"password": "secret"})


async def test_handoffs_requires_auth(client_and_db):
    client, db, store_id = client_and_db
    resp = await client.get("/api/handoffs")
    assert resp.status == 401


async def test_handoffs_list_and_resolve(client_and_db):
    client, db, store_id = client_and_db
    repo = HandoffRepository(db)
    hid = await repo.create(store_id, 555, "Айгуль", "Нужен менеджер", "Позовите человека")
    await _login(client)

    resp = await client.get("/api/handoffs")
    data = await resp.json()
    assert resp.status == 200
    assert len(data["handoffs"]) == 1
    assert data["handoffs"][0]["client_name"] == "Айгуль"
    assert data["handoffs"][0]["status"] == "new"

    resp2 = await client.post(f"/api/handoffs/{hid}/resolve")
    assert resp2.status == 200
    body = await resp2.json()
    assert body["handoff"]["status"] == "resolved"

    resp3 = await client.get("/api/handoffs", params={"status": "resolved"})
    data3 = await resp3.json()
    assert len(data3["handoffs"]) == 1


async def test_resolve_missing_handoff_returns_404(client_and_db):
    client, db, store_id = client_and_db
    await _login(client)
    resp = await client.post("/api/handoffs/999999/resolve")
    assert resp.status == 404
