"""Тесты тестового каталога."""

import pytest

from db.database import Database
from db.repositories import BouquetRepository, StoreRepository
from db.seed_catalog import seed_demo_catalog


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


async def test_seed_adds_fifteen_bouquets(db):
    store_id = await seed_demo_catalog(db, florist_chat_id=999)
    bouquets = await BouquetRepository(db).list_available(store_id)

    assert len(bouquets) == 15
    assert all(bouquet["price_kzt"] > 0 for bouquet in bouquets)
    assert len({bouquet["name"] for bouquet in bouquets}) == 15


async def test_seed_is_idempotent(db):
    first_id = await seed_demo_catalog(db, florist_chat_id=999)
    second_id = await seed_demo_catalog(db, florist_chat_id=999)

    assert first_id == second_id
    assert await BouquetRepository(db).count(first_id) == 15


async def test_seed_sets_florist_chat_id(db):
    store_id = await seed_demo_catalog(db, florist_chat_id=123456)

    store = await StoreRepository(db).get(store_id)
    assert store["florist_chat_id"] == 123456
