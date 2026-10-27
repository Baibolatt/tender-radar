"""Тесты диалога: автомат заказа, каталог, лимиты, история и сбой AI."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ai.client import AICompletion, AIUnavailable
from ai.schemas import AIResponse, OrderFields
from bot.services.datetime_parser import format_date_ru
from bot.services.dialog_service import DialogService
from bot.texts import (
    BOT_HONEST_TEXT,
    HANDOFF_TEXT,
    INPUT_TOO_LONG_TEXT,
    STALE_CONFIRM_TEXT,
)
from config import Settings
from db.database import Database
from db.repositories import (
    BouquetRepository,
    DialogSessionRepository,
    OutboxRepository,
    StoreRepository,
    UsageRepository,
)


class FakeAI:
    """Фейковая модель: тесты никогда не ходят в настоящий API."""

    def __init__(self, response=None, error=None):
        self.response = response or AIResponse(reply="Подскажу.")
        self.error = error
        self.calls = []

    async def complete(self, system_prompt, messages):
        self.calls.append((system_prompt, messages))
        if self.error is not None:
            raise self.error
        return AICompletion(
            response=self.response,
            prompt_tokens=10,
            completion_tokens=20,
            attempts=1,
        )


def _settings(**overrides):
    values = {
        "ai_api_key": "test-key",
        "timezone": "UTC",
        "max_input_chars": 2000,
        "max_response_chars": 900,
        "ready_minutes": 60,
        "catalog_page_size": 5,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


async def _make_store(db, **limits):
    stores = StoreRepository(db)
    bouquets = BouquetRepository(db)
    store_id = await stores.create(
        "TR", "Тестовый магазин", florist_chat_id=999, **limits
    )
    await bouquets.create(
        store_id,
        "Классические розы",
        15000,
        description="15 красных роз",
    )
    await bouquets.create(
        store_id, "Тюльпаны", 8500, description="15 тюльпанов"
    )
    return await stores.get(store_id)


def _today():
    return datetime.now(ZoneInfo("UTC")).date()


async def _order_count(db):
    cursor = await db.connection.execute("SELECT COUNT(*) AS count FROM orders")
    return (await cursor.fetchone())["count"]


async def _open_order(service, chat=555):
    await service.handle_callback(chat, "catalog:0")
    await service.handle_callback(chat, "bouquet:1")
    return await service.handle_callback(chat, "order:1")


async def _fill_order(service, chat=555, gift=True):
    await _open_order(service, chat)
    await service.reply(chat, "Айгуль")
    await service.reply(chat, "8 700 123 45 67")
    await service.reply(chat, "в подарок" if gift else "для себя")
    if gift:
        await service.reply(chat, "Алия")
        await service.reply(chat, "8 707 987 65 43")
    await service.reply(chat, "Тараз, ул. Абая, 10")
    await service.reply(chat, "завтра")
    await service.reply(chat, "с 14:00 до 16:00")
    await service.reply(chat, "Да")
    await service.reply(chat, "С днём рождения!")
    return await service.reply(chat, "нет")


async def _fill_until_card(service, chat=555):
    await _open_order(service, chat)
    await service.reply(chat, "Айгуль")
    await service.reply(chat, "8 700 123 45 67")
    await service.reply(chat, "для себя")
    await service.reply(chat, "Тараз, ул. Абая, 10")
    await service.reply(chat, "завтра")
    return await service.reply(chat, "с 14:00 до 16:00")


def _ready_response(bouquet_id=1):
    return AIResponse(
        reply="Проверьте заказ.",
        ready=True,
        order=OrderFields(
            client_name="Айгуль",
            phone="+77001234567",
            address="Тараз, ул. Абая, 10",
            delivery_date=(_today() + timedelta(days=1)).isoformat(),
            delivery_time="14:00-16:00",
            bouquet_id=bouquet_id,
            card_text="С днём рождения!",
        ),
    )


async def test_greeting_is_answered_by_code_without_ai(db):
    store = await _make_store(db)
    fake = FakeAI()
    service = DialogService(db, _settings(), fake, store)

    outcome = await service.reply(555, "Здравствуйте")

    assert "Здравствуйте" in outcome.reply
    assert outcome.keyboard == ((("Подобрать букет", "catalog:0"),),)
    assert fake.calls == []
    assert outcome.ai_calls == 0

    usage = await UsageRepository(db).get(store["id"], _today().isoformat())
    assert usage["dialogs"] == 1


async def test_bot_question_is_answered_honestly_without_ai(db):
    store = await _make_store(db)
    fake = FakeAI()
    service = DialogService(db, _settings(), fake, store)

    outcome = await service.reply(555, "Вы человек?")

    assert outcome.reply == BOT_HONEST_TEXT
    assert fake.calls == []


async def test_free_question_calls_ai_and_saves_history(db):
    store = await _make_store(db)
    fake = FakeAI(AIResponse(reply="Подскажу по доставке."))
    service = DialogService(db, _settings(), fake, store)

    outcome = await service.reply(555, "Помогите с заказом")

    assert outcome.reply == "Подскажу по доставке."
    assert outcome.handoff is False
    assert outcome.ai_calls == 1
    assert len(fake.calls) == 1

    system_prompt, messages = fake.calls[0]
    assert "Классические розы" in system_prompt
    assert "15 000 ₸" in system_prompt
    assert messages == [{"role": "user", "content": "Помогите с заказом"}]

    session = await DialogSessionRepository(db).get(store["id"], 555)
    assert session["ai_calls"] == 1
    assert json.loads(session["history_json"]) == [
        {"role": "user", "content": "Помогите с заказом"},
        {"role": "assistant", "content": "Подскажу по доставке."},
    ]

    usage = await UsageRepository(db).get(store["id"], _today().isoformat())
    assert usage["dialogs"] == 1
    assert usage["ai_calls"] == 1
    assert usage["prompt_tokens"] == 10
    assert usage["completion_tokens"] == 20


async def test_model_fields_are_merged_into_draft(db):
    store = await _make_store(db)
    fake = FakeAI(
        AIResponse(
            reply="Уточните адрес.",
            order=OrderFields(
                client_name="Айгуль",
                phone="+77001234567",
                bouquet_id=1,
                delivery_date=(_today() + timedelta(days=1)).isoformat(),
            ),
        )
    )
    service = DialogService(db, _settings(), fake, store)

    outcome = await service.reply(555, "Помогите с заказом")

    assert outcome.order_draft["client_name"] == "Айгуль"
    assert outcome.order_draft["phone"] == "+77001234567"
    assert outcome.order_draft["bouquet_id"] == 1
    assert outcome.order_draft["delivery_date"] == (
        _today() + timedelta(days=1)
    ).isoformat()

    session = await DialogSessionRepository(db).get(store["id"], 555)
    assert json.loads(session["draft_json"])["bouquet_id"] == 1


async def test_unavailable_bouquet_from_model_is_ignored(db):
    store = await _make_store(db)
    fake = FakeAI(
        AIResponse(reply="Уточните.", order=OrderFields(bouquet_id=999))
    )
    service = DialogService(db, _settings(), fake, store)

    outcome = await service.reply(555, "Помогите с заказом")

    assert "bouquet_id" not in outcome.order_draft


async def test_ai_failure_sends_handoff_and_does_not_crash(db):
    store = await _make_store(db)
    fake = FakeAI(error=AIUnavailable("timeout"))
    service = DialogService(db, _settings(), fake, store)

    outcome = await service.reply(555, "Помогите с заказом")

    assert outcome.reply == HANDOFF_TEXT
    assert outcome.handoff is True
    assert outcome.failed is True
    assert outcome.ai_calls == 1

    session = await DialogSessionRepository(db).get(store["id"], 555)
    assert session["state"] == "handoff"
    assert session["handoff_until"] is not None
    history = json.loads(session["history_json"])
    assert history[-1] == {"role": "assistant", "content": HANDOFF_TEXT}

    pending = await OutboxRepository(db).list_pending()
    assert len(pending) == 1
    assert pending[0].kind == "handoff"
    assert pending[0].chat_id == 999
    assert pending[0].payload["client_chat_id"] == 555
    assert pending[0].payload["reason"] == "Модель недоступна"


async def test_handoff_request_does_not_call_ai(db):
    store = await _make_store(db)
    fake = FakeAI()
    service = DialogService(db, _settings(), fake, store)

    outcome = await service.reply(555, "Позовите менеджера")

    assert outcome.reply == HANDOFF_TEXT
    assert outcome.handoff is True
    assert fake.calls == []
    assert len(await OutboxRepository(db).list_pending()) == 1


async def test_daily_counter_does_not_block_clients(db):
    store = await _make_store(db, daily_dialog_limit=1)
    fake = FakeAI()
    service = DialogService(db, _settings(), fake, store)

    first = await service.reply(111, "Привет")
    second = await service.reply(222, "Привет")

    assert first.blocked is False
    assert second.blocked is False
    assert second.handoff is False
    assert fake.calls == []

    usage = await UsageRepository(db).get(store["id"], _today().isoformat())
    assert usage["dialogs"] == 2

    assert await OutboxRepository(db).list_pending() == []


async def test_ai_call_counter_does_not_block_same_dialog(db):
    store = await _make_store(db, max_ai_calls_per_dialog=1)
    fake = FakeAI()
    service = DialogService(db, _settings(), fake, store)

    first = await service.reply(333, "Помогите с заказом")
    second = await service.reply(333, "Расскажите ещё")

    assert first.blocked is False
    assert second.blocked is False
    assert second.handoff is False
    assert len(fake.calls) == 2
    assert second.ai_calls == 2

    assert await OutboxRepository(db).list_pending() == []


async def test_too_long_input_does_not_call_ai(db):
    store = await _make_store(db)
    fake = FakeAI()
    service = DialogService(db, _settings(max_input_chars=5), fake, store)

    outcome = await service.reply(444, "Очень длинное сообщение")

    assert outcome.blocked is True
    assert outcome.reply == INPUT_TOO_LONG_TEXT
    assert fake.calls == []


async def test_long_response_is_truncated(db):
    store = await _make_store(db, max_response_chars=10)
    fake = FakeAI(AIResponse(reply="а" * 100))
    service = DialogService(db, _settings(), fake, store)

    outcome = await service.reply(555, "Помогите с заказом")

    assert outcome.reply == "а" * 10 + "…"


async def test_handoff_flag_is_saved(db):
    store = await _make_store(db)
    fake = FakeAI(AIResponse(reply="Передаю менеджеру.", handoff=True))
    service = DialogService(db, _settings(), fake, store)

    outcome = await service.reply(555, "Помогите с заказом")

    assert outcome.handoff is True
    session = await DialogSessionRepository(db).get(store["id"], 555)
    assert session["state"] == "handoff"
    assert session["handoff_until"] is not None

    pending = await OutboxRepository(db).list_pending()
    assert len(pending) == 1
    assert pending[0].kind == "handoff"
    assert (
        pending[0].payload["reason"]
        == "Модель передала диалог менеджеру"
    )


async def test_catalog_shows_code_built_list_and_buttons(db):
    store = await _make_store(db)
    fake = FakeAI()
    service = DialogService(db, _settings(), fake, store)

    outcome = await service.handle_callback(555, "catalog:0")

    assert "Классические розы" in outcome.reply
    assert "15 000 ₸" in outcome.reply
    assert "В каталоге есть, наличие подтвердит флорист." in outcome.reply
    assert (("Классические розы", "bouquet:1"),) in outcome.keyboard
    assert (("Тюльпаны", "bouquet:2"),) in outcome.keyboard
    assert fake.calls == []


async def test_catalog_card_has_order_button(db):
    store = await _make_store(db)
    fake = FakeAI()
    service = DialogService(db, _settings(), fake, store)

    outcome = await service.handle_callback(555, "bouquet:1")

    assert "15 красных роз" in outcome.reply
    assert (
        ("Заказать", "order:1"),
        ("Назад к списку", "catalog:0"),
    ) in outcome.keyboard
    assert fake.calls == []


async def test_catalog_filters_by_budget(db):
    store = await _make_store(db)
    fake = FakeAI()
    service = DialogService(db, _settings(), fake, store)

    outcome = await service.reply(555, "Покажите букеты до 10 000")

    assert "Варианты до 10 000 ₸" in outcome.reply
    assert "Тюльпаны" in outcome.reply
    assert "Классические розы" not in outcome.reply
    assert (("Тюльпаны", "bouquet:2"),) in outcome.keyboard
    assert fake.calls == []


async def test_catalog_over_budget_offers_nearest(db):
    store = await _make_store(db)
    fake = FakeAI()
    service = DialogService(db, _settings(), fake, store)

    outcome = await service.reply(555, "Покажите букеты до 5 000")

    assert "В бюджете до 5 000 ₸ подходящих нет" in outcome.reply
    assert "Тюльпаны" in outcome.reply
    assert fake.calls == []


async def test_card_question_has_buttons_and_saves_exact_text(db):
    store = await _make_store(db)
    fake = FakeAI()
    service = DialogService(db, _settings(), fake, store)

    asked = await _fill_until_card(service)

    assert asked.reply == "Нужна открытка?"
    assert asked.keyboard == ((("Да", "card:yes"), ("Нет", "card:no")),)

    text_question = await service.handle_callback(555, "card:yes")

    assert text_question.reply == "Что написать в открытке?"
    assert text_question.keyboard is None

    saved = await service.reply(555, "С днём рождения, Алия!")

    assert saved.order_draft["card_text"] == "С днём рождения, Алия!"
    assert saved.order_draft["card_needed"] is True
    assert "пожелания по оформлению" in saved.reply
    assert fake.calls == []


async def test_card_no_button_skips_text_question(db):
    store = await _make_store(db)
    fake = FakeAI()
    service = DialogService(db, _settings(), fake, store)

    await _fill_until_card(service)
    skipped = await service.handle_callback(555, "card:no")

    assert skipped.order_draft["card_text"] == ""
    assert skipped.order_draft["card_asked"] is True
    assert "пожелания по оформлению" in skipped.reply
    assert fake.calls == []


async def test_full_order_flow_without_ai_and_separate_recipient(db):
    store = await _make_store(db)
    fake = FakeAI()
    service = DialogService(db, _settings(), fake, store)

    opened = await _open_order(service)
    assert "Как вас зовут?" in opened.reply

    summary = await _fill_order(service)

    assert "Проверьте заказ:" in summary.reply
    assert "Заказчик: Айгуль, +77001234567" in summary.reply
    assert "Получатель: Алия, +77079876543" in summary.reply
    assert "14:00–16:00" in summary.reply
    assert "Открытка: «С днём рождения!»" in summary.reply
    assert summary.reply.endswith("Всё верно?")
    assert (
        ("Всё верно", "confirm:yes"),
        ("Изменить", "confirm:edit"),
    ) in summary.keyboard
    assert fake.calls == []
    assert await _order_count(db) == 0

    accepted = await service.handle_callback(555, "confirm:yes")

    assert accepted.order is not None
    assert accepted.order.public_number == "TR-000001"
    assert accepted.order.phone == "+77001234567"
    assert accepted.order.recipient_name == "Алия"
    assert accepted.order.recipient_phone == "+77079876543"
    assert accepted.order.is_gift is True
    assert "Заказ оформлен!" in accepted.reply
    assert "TR-000001" in accepted.reply
    assert "примерно через час" in accepted.reply
    assert fake.calls == []

    session = await DialogSessionRepository(db).get(store["id"], 555)
    assert session["state"] == "chatting"
    assert json.loads(session["draft_json"]) == {}

    pending = await OutboxRepository(db).list_pending()
    assert len(pending) == 1
    assert pending[0].order_id == accepted.order.id


async def test_repeated_order_and_confirm_do_not_create_duplicates(db):
    store = await _make_store(db)
    fake = FakeAI()
    service = DialogService(db, _settings(), fake, store)

    await _open_order(service)
    # Повторное нажатие «Заказать» не создаёт второй заказ.
    again = await service.handle_callback(555, "order:1")
    assert "Как вас зовут?" in again.reply
    assert await _order_count(db) == 0

    await _fill_order(service)
    first = await service.handle_callback(555, "confirm:yes")
    second = await service.handle_callback(555, "confirm:yes")

    assert first.order is not None
    assert second.order is None
    assert second.reply == STALE_CONFIRM_TEXT
    assert await _order_count(db) == 1
    assert len(await OutboxRepository(db).list_pending()) == 1


async def test_question_inside_confirmation_answers_then_shows_summary(db):
    store = await _make_store(db)
    fake = FakeAI(AIResponse(reply="Можно добавить открытку."))
    service = DialogService(db, _settings(), fake, store)

    await _fill_order(service)
    outcome = await service.reply(555, "А можно с открыткой?", 20)

    assert outcome.reply.startswith("Можно добавить открытку.")
    assert outcome.reply.endswith("Всё верно?")
    assert (
        ("Всё верно", "confirm:yes"),
        ("Изменить", "confirm:edit"),
    ) in outcome.keyboard
    assert len(fake.calls) == 1

    session = await DialogSessionRepository(db).get(store["id"], 555)
    assert session["state"] == "confirming"
    assert await _order_count(db) == 0


async def test_ready_time_question_inside_confirmation_uses_config(db):
    store = await _make_store(db)
    fake = FakeAI()
    service = DialogService(db, _settings(ready_minutes=60), fake, store)

    await _fill_order(service)
    outcome = await service.reply(555, "Когда будет готово?", 20)

    assert "примерно через час" in outcome.reply
    assert outcome.reply.endswith("Всё верно?")
    assert fake.calls == []

    session = await DialogSessionRepository(db).get(store["id"], 555)
    assert session["state"] == "confirming"


async def test_edit_menu_changes_delivery_date(db):
    store = await _make_store(db)
    fake = FakeAI()
    service = DialogService(db, _settings(), fake, store)

    await _fill_order(service)
    menu = await service.handle_callback(555, "confirm:edit")
    assert menu.reply == "Что поправить?"

    asked = await service.handle_callback(555, "edit:delivery_date")
    assert "На когда нужен букет?" in asked.reply

    summary = await service.reply(555, "послезавтра")
    new_date = _today() + timedelta(days=2)
    assert format_date_ru(new_date) in summary.reply

    accepted = await service.handle_callback(555, "confirm:yes")
    assert accepted.order is not None
    assert accepted.order.delivery_date == new_date.isoformat()
    assert fake.calls == []


async def test_field_change_by_text_inside_confirmation(db):
    store = await _make_store(db)
    fake = FakeAI()
    service = DialogService(db, _settings(), fake, store)

    await _fill_order(service)
    outcome = await service.reply(555, "дата послезавтра", 21)

    new_date = _today() + timedelta(days=2)
    assert format_date_ru(new_date) in outcome.reply
    assert outcome.reply.endswith("Всё верно?")
    assert fake.calls == []


async def test_bad_phone_is_asked_again_softly(db):
    store = await _make_store(db)
    fake = FakeAI()
    service = DialogService(db, _settings(), fake, store)

    await _open_order(service)
    await service.reply(555, "Айгуль")
    # После +7 первая цифра не 7 — номер не принимаем.
    outcome = await service.reply(555, "8 100 123 45 67")

    assert "ошибка" in outcome.reply
    assert "после +7 должно быть 10 цифр" in outcome.reply
    assert "+7 707 123 45 67" in outcome.reply
    session = await DialogSessionRepository(db).get(store["id"], 555)
    assert session["state"] == "order_client_phone"

    next_step = await service.reply(555, "8 707 123 45 67")
    assert "для вас или в подарок" in next_step.reply
    assert next_step.order_draft["phone"] == "+77071234567"


async def test_free_question_during_flow_answers_and_repeats_step(db):
    store = await _make_store(db)
    fake = FakeAI(AIResponse(reply="Доставка по Таразу — 1000 ₸."))
    service = DialogService(db, _settings(), fake, store)

    await _open_order(service)
    outcome = await service.reply(555, "А сколько стоит доставка?")

    assert outcome.reply.startswith("Доставка по Таразу — 1000 ₸.")
    assert outcome.reply.endswith("Как вас зовут?")
    assert len(fake.calls) == 1

    session = await DialogSessionRepository(db).get(store["id"], 555)
    assert session["state"] == "order_client_name"
    assert json.loads(session["draft_json"])["bouquet_id"] == 1


async def test_model_ready_shows_summary_and_waits_for_button(db):
    store = await _make_store(db)
    fake = FakeAI(_ready_response(1))
    service = DialogService(db, _settings(), fake, store)

    outcome = await service.reply(555, "Помогите с заказом", 10)

    assert outcome.order is None
    assert "Проверьте заказ:" in outcome.reply
    assert "«Классические розы» — 15 000 ₸" in outcome.reply
    assert outcome.reply.endswith("Всё верно?")
    assert await _order_count(db) == 0

    session = await DialogSessionRepository(db).get(store["id"], 555)
    assert session["state"] == "confirming"
    assert json.loads(session["draft_json"])["bouquet_id"] == 1


async def test_ready_flag_with_incomplete_draft_is_ignored(db):
    store = await _make_store(db)
    fake = FakeAI(
        AIResponse(
            reply="Уточните, пожалуйста, адрес.",
            ready=True,
            order=OrderFields(bouquet_id=1),
        )
    )
    service = DialogService(db, _settings(), fake, store)

    outcome = await service.reply(555, "Помогите с заказом", 10)

    assert outcome.order is None
    assert outcome.reply == "Уточните, пожалуйста, адрес."
    session = await DialogSessionRepository(db).get(store["id"], 555)
    assert session["state"] == "chatting"
