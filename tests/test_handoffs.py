"""Тесты репозитория обращений флористу."""

import pytest

from db.database import Database
from db.repositories import HandoffRepository, StoreRepository


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


async def _store(db):
    return await StoreRepository(db).create("TR", "Магазин", florist_chat_id=999)


async def test_create_and_list_handoff(db):
    store_id = await _store(db)
    repo = HandoffRepository(db)

    handoff_id = await repo.create(store_id, 555, "Айгуль", "Нужен менеджер", "Позовите человека")

    rows = await repo.list(store_id)
    assert len(rows) == 1
    assert rows[0]["id"] == handoff_id
    assert rows[0]["client_name"] == "Айгуль"
    assert rows[0]["status"] == "new"
    assert rows[0]["last_message"] == "Позовите человека"


async def test_list_filters_by_status(db):
    store_id = await _store(db)
    repo = HandoffRepository(db)
    await repo.create(store_id, 555, "Айгуль", "причина", "сообщение")

    assert await repo.list(store_id, status="new") != []
    assert await repo.list(store_id, status="resolved") == []


async def test_resolve_handoff(db):
    store_id = await _store(db)
    repo = HandoffRepository(db)
    handoff_id = await repo.create(store_id, 555, "Айгуль", "причина", "сообщение")

    row = await repo.resolve(handoff_id)
    assert row is not None and row["status"] == "resolved"
    assert (await repo.list(store_id, status="resolved"))[0]["id"] == handoff_id
    assert await repo.resolve(999999) is None
