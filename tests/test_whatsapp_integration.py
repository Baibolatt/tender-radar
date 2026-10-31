"""Интеграционные тесты полного пути WhatsApp.

Проверяют цепочку «aiohttp-вебхук → ConversationService → клиент WhatsApp»
без сети и без настоящей модели: AI заменён фейком, отправка — заглушкой.
Нажатий кнопок в WhatsApp ещё нет, поэтому заказ ведём текстовыми ответами.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from ai.client import AICompletion
from ai.schemas import AIResponse
from bot.services.conversation_service import ConversationService
from bot.texts import HANDOFF_TEXT, START_TEXT
from bot.whatsapp_webhook import KEYBOARD_PROMPT, create_whatsapp_app
from config import Settings
from db.database import Database
from db.repositories import (
    BouquetRepository,
    HandoffRepository,
    OutboxRepository,
    StoreRepository,
)

SENDER = "77001234567"
CANONICAL_SENDER = "+77001234567"
VERIFY_TOKEN = "TEST_TOKEN"


class FakeAI:
    """Фейковая модель: тесты никогда не ходят в настоящий API."""

    def __init__(self, response: AIResponse | None = None) -> None:
        self.response = response or AIResponse(reply="Подскажу.")
        self.calls: list[tuple[str, list[dict]]] = []

    async def complete(self, system_prompt, messages):
        self.calls.append((system_prompt, messages))
        return AICompletion(
            response=self.response,
            prompt_tokens=10,
            completion_tokens=20,
            attempts=1,
        )


class StubWhatsAppClient:
    """Заглушка клиента: записывает отправки и ничего не шлёт в сеть."""

    def __init__(self) -> None:
        self.text_calls: list[tuple[str, str]] = []
        self.interactive_calls: list[tuple[str, str, list[dict]]] = []

    async def send_text(self, to: str, text: str) -> dict:
        self.text_calls.append((to, text))
        return {}

    async def send_interactive(
        self, to: str, text: str, buttons: list[dict]
    ) -> dict:
        self.interactive_calls.append((to, text, buttons))
        return {}


def _settings(**overrides) -> Settings:
    values = {
        "ai_api_key": "test-key",
        "timezone": "UTC",
        "max_input_chars": 2000,
        "max_response_chars": 900,
        "ready_minutes": 60,
        "catalog_page_size": 5,
        "ai_step_questions": False,
        "webhook_verify_token": VERIFY_TOKEN,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


async def _make_store(db) -> dict:
    stores = StoreRepository(db)
    bouquets = BouquetRepository(db)
    store_id = await stores.create(
        "TR", "Тестовый магазин", florist_chat_id=999
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


def _text_payload(
    text: str,
    message_id: str,
    sender: str = SENDER,
) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": sender,
                                    "id": message_id,
                                    "text": {"body": text},
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }


@pytest.fixture
async def whatsapp(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await db.migrate()

    store = await _make_store(db)
    settings = _settings()
    service = ConversationService(db, settings, FakeAI(), store)
    stub = StubWhatsAppClient()
    app = create_whatsapp_app(settings, service, stub)

    async with TestClient(TestServer(app)) as client:
        yield client, stub, db, store
    await db.close()


async def _send(client, text: str, message_id: str) -> None:
    resp = await client.post(
        "/webhook", json=_text_payload(text, message_id)
    )
    assert resp.status == 200


async def _order_count(db) -> int:
    cursor = await db.connection.execute(
        "SELECT COUNT(*) AS count FROM orders"
    )
    return (await cursor.fetchone())["count"]


async def test_incoming_text_is_answered_by_greeting(whatsapp):
    client, stub, db, store = whatsapp

    await _send(client, "Здравствуйте", "wamid.greet.1")

    assert len(stub.text_calls) == 1
    to, text = stub.text_calls[0]
    assert to == SENDER
    assert text == START_TEXT
    # Кнопка каталога ушла отдельным интерактивным сообщением.
    assert len(stub.interactive_calls) == 1
    assert stub.interactive_calls[0][1] == KEYBOARD_PROMPT


async def test_phone_spellings_share_one_session(whatsapp):
    client, stub, db, store = whatsapp

    # Один клиент пишет сначала цифрами, потом с ведущим плюсом.
    await client.post(
        "/webhook",
        json=_text_payload("Здравствуйте", "wamid.spell.1", sender=SENDER),
    )
    await client.post(
        "/webhook",
        json=_text_payload(
            "Здравствуйте", "wamid.spell.2", sender=CANONICAL_SENDER
        ),
    )

    cursor = await db.connection.execute(
        "SELECT chat_id FROM dialog_sessions WHERE channel = 'whatsapp'"
    )
    rows = await cursor.fetchall()
    # Сессия одна и хранится в каноничном виде.
    assert [row["chat_id"] for row in rows] == [CANONICAL_SENDER]
    # Ответы всегда уходят цифрами: Graph API не принимает "+".
    assert [to for to, _ in stub.text_calls] == [SENDER, SENDER]


async def test_full_order_flow_via_text_creates_order(whatsapp):
    client, stub, db, store = whatsapp

    # Прогрев: «заказать» → вопрос о поводе и бюджете.
    await _send(client, "заказать", "wamid.order.1")
    # Повод → каталог с кнопками букетов.
    await _send(client, "день рождения", "wamid.order.2")
    assert "Классические розы" in stub.text_calls[-1][1]
    bouquet_buttons = {
        button["id"]
        for _, _, buttons in stub.interactive_calls
        for button in buttons
    }
    assert "bouquet:1" in bouquet_buttons

    # Текстом выбираем букет вместо нажатия кнопки.
    await _send(client, "Классические розы", "wamid.order.3")
    assert "Как вас зовут?" in stub.text_calls[-1][1]

    await _send(client, "Айгуль", "wamid.order.4")
    await _send(client, "8 700 123 45 67", "wamid.order.5")
    await _send(client, "для себя", "wamid.order.6")
    await _send(client, "Тараз, ул. Абая, 10", "wamid.order.7")
    await _send(client, "завтра", "wamid.order.8")
    await _send(client, "с 14:00 до 16:00", "wamid.order.9")

    # Вопрос про открытку уходит с кнопками «Да/Нет».
    card_buttons = {
        button["id"]
        for _, _, buttons in stub.interactive_calls
        for button in buttons
    }
    assert {"card:yes", "card:no"} <= card_buttons

    # Кнопку нажать нельзя — отвечаем текстом.
    await _send(client, "да", "wamid.order.10")
    assert "Что написать в открытке?" in stub.text_calls[-1][1]
    await _send(client, "С днём рождения!", "wamid.order.11")
    await _send(client, "нет", "wamid.order.12")

    # Сводка с кнопками подтверждения.
    assert "Проверьте заказ:" in stub.text_calls[-1][1]
    summary_buttons = {
        button["id"]
        for _, _, buttons in stub.interactive_calls
        for button in buttons
    }
    assert {"confirm:yes", "confirm:edit"} <= summary_buttons
    assert await _order_count(db) == 0

    # Подтверждаем текстом «да».
    await _send(client, "да", "wamid.order.13")

    assert "Заказ оформлен!" in stub.text_calls[-1][1]
    assert "TR-000001" in stub.text_calls[-1][1]
    assert await _order_count(db) == 1

    cursor = await db.connection.execute("SELECT * FROM orders")
    row = await cursor.fetchone()
    assert row["public_number"] == "TR-000001"
    assert row["channel"] == "whatsapp"
    assert row["client_chat_id"] == CANONICAL_SENDER
    assert row["source_update_id"] == "wamid.order.13"
    assert row["bouquet_name"] == "Классические розы"

    # Флористу поставлено уведомление о заказе.
    pending = await OutboxRepository(db).list_pending()
    assert len(pending) == 1
    assert pending[0].kind == "order_notification"
    assert pending[0].chat_id == 999


async def test_handoff_request_is_logged_and_enqueued(whatsapp):
    client, stub, db, store = whatsapp

    await _send(client, "позовите человека", "wamid.handoff.1")

    assert len(stub.text_calls) == 1
    assert stub.text_calls[0] == (SENDER, HANDOFF_TEXT)

    handoffs = await HandoffRepository(db).list(int(store["id"]))
    assert len(handoffs) == 1
    assert handoffs[0]["channel"] == "whatsapp"
    assert handoffs[0]["client_chat_id"] == CANONICAL_SENDER
    assert handoffs[0]["status"] == "new"

    pending = await OutboxRepository(db).list_pending()
    assert len(pending) == 1
    assert pending[0].kind == "handoff"
    assert pending[0].chat_id == 999
    assert pending[0].payload["client_chat_id"] == CANONICAL_SENDER
    assert isinstance(pending[0].payload["client_chat_id"], str)


async def test_non_text_message_is_ignored(whatsapp):
    client, stub, db, store = whatsapp

    # Статус доставки и интерактивное сообщение (нажатие кнопки) — не текст.
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.status.1",
                                    "status": "delivered",
                                }
                            ],
                            "messages": [
                                {
                                    "from": SENDER,
                                    "id": "wamid.button.1",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {
                                            "id": "card:yes",
                                            "title": "Да",
                                        },
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }

    resp = await client.post("/webhook", json=payload)

    assert resp.status == 200
    assert stub.text_calls == []
    assert stub.interactive_calls == []


async def test_invalid_verify_token_returns_403(whatsapp):
    client, stub, db, store = whatsapp

    resp = await client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "WRONG_TOKEN",
            "hub.challenge": "12345",
        },
    )

    assert resp.status == 403


async def test_empty_and_malformed_payload_return_200_without_send(whatsapp):
    client, stub, db, store = whatsapp

    empty = await client.post("/webhook", json={})
    malformed = await client.post(
        "/webhook",
        data=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    no_body = await client.post("/webhook", data=b"")

    assert empty.status == 200
    assert malformed.status == 200
    assert no_body.status == 200
    assert stub.text_calls == []
    assert stub.interactive_calls == []
