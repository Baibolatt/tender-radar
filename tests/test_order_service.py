"""Тесты проверки полей заказа и сохранения."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from bot.services.order_service import OrderService, OrderValidationError
from config import Settings
from db.database import Database
from db.repositories import (
    BouquetRepository,
    OutboxRepository,
    StoreRepository,
)


def _settings(**overrides):
    values = {
        "ai_api_key": "test-key",
        "timezone": "UTC",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _today():
    return datetime.now(ZoneInfo("UTC")).date()


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


async def _make_store(db, price=15000, available=True, florist_chat_id=999):
    stores = StoreRepository(db)
    bouquets = BouquetRepository(db)
    store_id = await stores.create(
        "TR", "Тестовый магазин", florist_chat_id=florist_chat_id
    )
    bouquet_id = await bouquets.create(
        store_id,
        "Классические розы",
        price,
        description="15 красных роз",
        is_available=available,
    )
    store = await stores.get(store_id)
    return store, bouquet_id


def _complete_draft(bouquet_id, **overrides):
    values = {
        "client_name": "Айгуль",
        "phone": "8 700 123 45 67",
        "address": "Тараз, ул. Абая, 10",
        "delivery_date": (_today() + timedelta(days=1)).isoformat(),
        "bouquet_id": bouquet_id,
        "delivery_time": "14:00-16:00",
        "card_text": "С днём рождения!",
    }
    values.update(overrides)
    return values


async def test_validate_complete_draft(db):
    store, bouquet_id = await _make_store(db)
    service = OrderService(db, _settings(), store)

    result = await service.validate(_complete_draft(bouquet_id))

    assert result.ok is True
    assert result.draft["phone"] == "+77001234567"
    assert result.draft["bouquet_id"] == bouquet_id
    assert result.draft["delivery_time"] == "14:00-16:00"


async def test_validate_reports_missing_fields(db):
    store, _ = await _make_store(db)
    service = OrderService(db, _settings(), store)

    result = await service.validate({})

    assert result.ok is False
    assert set(result.missing) == {
        "имя",
        "телефон",
        "адрес",
        "дата доставки",
        "время доставки",
        "букет",
    }
    assert "не хватает" in result.hint()


async def test_validate_gift_requires_separate_recipient(db):
    store, bouquet_id = await _make_store(db)
    service = OrderService(db, _settings(), store)
    draft = _complete_draft(bouquet_id, is_gift=True)

    result = await service.validate(draft)
    assert "имя получателя" in result.missing
    assert "телефон получателя" in result.missing

    draft["recipient_name"] = "Алия"
    draft["recipient_phone"] = "8 707 987 65 43"
    result = await service.validate(draft)
    assert result.ok is True
    assert result.draft["recipient_phone"] == "+77079876543"
    assert result.draft["phone"] == "+77001234567"


async def test_validate_rejects_bad_phone(db):
    store, bouquet_id = await _make_store(db)
    service = OrderService(db, _settings(), store)

    result = await service.validate(_complete_draft(bouquet_id, phone="123"))

    assert result.ok is False
    assert "телефон указан неверно" in result.errors


async def test_validate_rejects_past_and_far_dates(db):
    store, bouquet_id = await _make_store(db)
    service = OrderService(db, _settings(), store)

    past = await service.validate(
        _complete_draft(bouquet_id, delivery_date=(_today() - timedelta(days=1)).isoformat())
    )
    far = await service.validate(
        _complete_draft(bouquet_id, delivery_date=(_today() + timedelta(days=61)).isoformat())
    )

    assert "дата доставки уже прошла" in past.errors
    assert "дата доставки слишком далеко" in far.errors


async def test_validate_rejects_unavailable_bouquet(db):
    store, bouquet_id = await _make_store(db, available=False)
    service = OrderService(db, _settings(), store)

    result = await service.validate(_complete_draft(bouquet_id))

    assert result.ok is False
    assert "выбранный букет недоступен" in result.errors


async def test_summary_uses_price_from_db(db):
    store, bouquet_id = await _make_store(db, price=17500)
    service = OrderService(db, _settings(), store)

    summary = await service.summary(_complete_draft(bouquet_id))

    assert "«Классические розы»" in summary
    assert "17 500 ₸" in summary
    assert "+77001234567" in summary
    assert "Получатель: заказчик" in summary
    assert summary.endswith("Всё верно?")


async def test_summary_shows_normalized_client_phone(db):
    store, bouquet_id = await _make_store(db)
    service = OrderService(db, _settings(), store)

    summary = await service.summary(
        _complete_draft(bouquet_id, phone="7 700 123 45 67")
    )

    assert "Заказчик: Айгуль, +77001234567" in summary


async def test_create_saves_order_and_outbox(db):
    store, bouquet_id = await _make_store(db)
    service = OrderService(db, _settings(), store)

    order = await service.create(
        _complete_draft(bouquet_id), chat_id=555, source_update_id=42
    )

    assert order.public_number == "TR-000001"
    assert order.price_kzt == 15000
    assert order.bouquet_name == "Классические розы"
    assert order.phone == "+77001234567"

    pending = await OutboxRepository(db).list_pending()
    assert len(pending) == 1
    assert pending[0].order_id == order.id


async def test_summary_shows_human_date_and_time(db):
    store, bouquet_id = await _make_store(db)
    service = OrderService(db, _settings(), store)
    draft = _complete_draft(bouquet_id)

    summary = await service.summary(draft)

    assert draft["delivery_date"] not in summary
    assert "Время: 14:00-16:00" in summary


async def test_create_is_idempotent_by_flow_id(db):
    store, bouquet_id = await _make_store(db)
    service = OrderService(db, _settings(), store)
    draft = _complete_draft(bouquet_id)
    draft["flow_id"] = "flow-abc"

    first = await service.create(draft, chat_id=555, source_update_id=1)
    second = await service.create(draft, chat_id=555, source_update_id=2)

    assert second.id == first.id
    assert len(await OutboxRepository(db).list_pending()) == 1


async def test_ready_text_uses_config(db):
    store, _ = await _make_store(db)
    service = OrderService(db, _settings(ready_minutes=60), store)

    assert "примерно через час" in service.ready_text()


async def test_create_is_idempotent_by_update_id(db):
    store, bouquet_id = await _make_store(db)
    service = OrderService(db, _settings(), store)
    draft = _complete_draft(bouquet_id)

    first = await service.create(draft, chat_id=555, source_update_id=42)
    second = await service.create(draft, chat_id=555, source_update_id=42)

    assert second.id == first.id
    assert len(await OutboxRepository(db).list_pending()) == 1


async def test_create_rejects_incomplete_draft(db):
    store, _ = await _make_store(db)
    service = OrderService(db, _settings(), store)

    with pytest.raises(OrderValidationError):
        await service.create({}, chat_id=555, source_update_id=42)


async def test_create_requires_florist_chat(db):
    store, bouquet_id = await _make_store(db, florist_chat_id=None)
    service = OrderService(db, _settings(), store)

    with pytest.raises(OrderValidationError):
        await service.create(
            _complete_draft(bouquet_id), chat_id=555, source_update_id=42
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+7 (700) 123-45-67", "+77001234567"),
        ("8 700 123 45 67", "+77001234567"),
        ("7 700 123 45 67", "+77001234567"),
        ("+7 700 123 45 67", "+77001234567"),
        ("7001234567", "+77001234567"),
        # После +7 первая цифра не 7 — номер неверный.
        ("8 100 123 45 67", None),
        ("7 100 123 45 67", None),
        ("123", None),
        ("70012345", None),
        ("", None),
    ],
)
def test_normalize_phone(raw, expected):
    assert OrderService.normalize_phone(raw) == expected
