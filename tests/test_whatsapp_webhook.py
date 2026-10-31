"""Тесты вебхука WhatsApp: проверка токена, приём сообщений и ошибки."""

import asyncio
import logging

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.services.conversation_service import ConversationResult
from bot.services.whatsapp_client import WhatsAppUnavailable
from bot.whatsapp_webhook import KEYBOARD_PROMPT, create_whatsapp_app
from config import Settings

VERIFY_TOKEN = "TEST_TOKEN"
SENDER = "77001234567"
CANONICAL_SENDER = "+77001234567"


def _settings(token: str = VERIFY_TOKEN) -> Settings:
    return Settings(_env_file=None, webhook_verify_token=token)


def _payload(
    text: str = "Привет",
    sender: str = SENDER,
    message_id: str = "wamid.1",
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
                                    "timestamp": "1234567890",
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }


class FakeConversationService:
    """Заглушка ConversationService для проверки вызовов."""

    def __init__(
        self,
        result: ConversationResult | None = None,
        delay: float = 0.0,
    ) -> None:
        self.result = result or ConversationResult(reply="Здравствуйте!")
        self.delay = delay
        self.raises: Exception | None = None
        self.calls: list[dict] = []
        self.active = 0
        self.max_active = 0

    async def process_message(
        self, user_id: str, text: str, channel: str, source_id: str
    ) -> ConversationResult:
        self.calls.append(
            {
                "user_id": user_id,
                "text": text,
                "channel": channel,
                "source_id": source_id,
            }
        )
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        finally:
            self.active -= 1
        if self.raises is not None:
            raise self.raises
        return self.result


class FakeWhatsAppClient:
    """Заглушка клиента WhatsApp для проверки отправленных сообщений."""

    def __init__(self) -> None:
        self.text_calls: list[tuple[str, str]] = []
        self.interactive_calls: list[tuple[str, str, list[dict]]] = []
        self.events: list[str] = []
        self.raises: Exception | None = None

    async def send_text(self, to: str, text: str) -> dict:
        self.events.append("text")
        self.text_calls.append((to, text))
        if self.raises is not None:
            raise self.raises
        return {}

    async def send_interactive(
        self, to: str, text: str, buttons: list[dict]
    ) -> dict:
        self.events.append("interactive")
        self.interactive_calls.append((to, text, buttons))
        if self.raises is not None:
            raise self.raises
        return {}


@pytest.fixture
async def webhook():
    settings = _settings()
    service = FakeConversationService()
    whatsapp = FakeWhatsAppClient()
    app = create_whatsapp_app(settings, service, whatsapp)
    async with TestClient(TestServer(app)) as client:
        yield client, service, whatsapp


async def test_get_webhook_valid_token_returns_challenge(webhook):
    client, service, whatsapp = webhook
    resp = await client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "12345",
        },
    )
    assert resp.status == 200
    assert await resp.text() == "12345"


async def test_get_webhook_invalid_token_returns_403(webhook):
    client, service, whatsapp = webhook
    resp = await client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "WRONG"},
    )
    assert resp.status == 403


async def test_get_webhook_invalid_token_does_not_log_secret(webhook, caplog):
    client, service, whatsapp = webhook
    with caplog.at_level(logging.DEBUG):
        resp = await client.get(
            "/webhook",
            params={"hub.mode": "subscribe", "hub.verify_token": "WRONG"},
        )
    assert resp.status == 403
    assert VERIFY_TOKEN not in caplog.text


async def test_text_message_calls_service_and_sends_reply(webhook):
    client, service, whatsapp = webhook
    resp = await client.post(
        "/webhook", json=_payload(text="Привет", message_id="wamid.42")
    )

    assert resp.status == 200
    assert service.calls == [
        {
            "user_id": CANONICAL_SENDER,
            "text": "Привет",
            "channel": "whatsapp",
            "source_id": "wamid.42",
        }
    ]
    assert whatsapp.text_calls == [(SENDER, "Здравствуйте!")]
    assert whatsapp.interactive_calls == []
    assert whatsapp.events == ["text"]


async def test_keyboard_sent_as_interactive_follow_up(webhook):
    client, service, whatsapp = webhook
    service.result = ConversationResult(
        reply="Подтвердить заказ?",
        keyboard=((("Да", "card:yes"), ("Нет", "card:no")),),
    )

    resp = await client.post("/webhook", json=_payload())

    assert resp.status == 200
    assert whatsapp.events == ["text", "interactive"]
    to, text, buttons = whatsapp.interactive_calls[0]
    assert to == SENDER
    assert text == KEYBOARD_PROMPT
    assert buttons == [
        {"title": "Да", "id": "card:yes"},
        {"title": "Нет", "id": "card:no"},
    ]


async def test_keyboard_limited_to_three_buttons_and_20_chars(webhook):
    client, service, whatsapp = webhook
    long_title = "Очень длинное название кнопки"
    service.result = ConversationResult(
        reply="Выберите",
        keyboard=(
            (("Раз", "a:1"), ("Два", "a:2")),
            ((long_title, "a:3"), ("Четыре", "a:4")),
        ),
    )

    resp = await client.post("/webhook", json=_payload())

    assert resp.status == 200
    buttons = whatsapp.interactive_calls[0][2]
    assert [button["id"] for button in buttons] == ["a:1", "a:2", "a:3"]
    assert all(len(button["title"]) <= 20 for button in buttons)


async def test_empty_payload_returns_200_without_calls(webhook):
    client, service, whatsapp = webhook
    resp = await client.post("/webhook", json={})

    assert resp.status == 200
    assert service.calls == []
    assert whatsapp.text_calls == []


async def test_entry_without_messages_returns_200_without_calls(webhook):
    client, service, whatsapp = webhook
    payload = {"entry": [{"changes": [{"value": {"statuses": []}}]}]}
    resp = await client.post("/webhook", json=payload)

    assert resp.status == 200
    assert service.calls == []
    assert whatsapp.text_calls == []


async def test_malformed_json_returns_200_without_calls(webhook):
    client, service, whatsapp = webhook
    resp = await client.post(
        "/webhook",
        data=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )

    assert resp.status == 200
    assert service.calls == []
    assert whatsapp.text_calls == []


async def test_empty_body_returns_200_without_calls(webhook):
    client, service, whatsapp = webhook
    resp = await client.post("/webhook", data=b"")

    assert resp.status == 200
    assert service.calls == []
    assert whatsapp.text_calls == []


async def test_non_text_message_is_ignored(webhook):
    client, service, whatsapp = webhook
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": SENDER,
                                    "id": "wamid.media",
                                    "type": "image",
                                    "image": {"id": "media-1"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    resp = await client.post("/webhook", json=payload)

    assert resp.status == 200
    assert service.calls == []
    assert whatsapp.text_calls == []


async def test_whatsapp_client_error_returns_200_and_logs(webhook, caplog):
    client, service, whatsapp = webhook
    whatsapp.raises = WhatsAppUnavailable("сеть недоступна")

    with caplog.at_level(logging.ERROR):
        resp = await client.post("/webhook", json=_payload())

    assert resp.status == 200
    assert len(service.calls) == 1
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


async def test_conversation_service_error_returns_200_and_logs(
    webhook, caplog
):
    client, service, whatsapp = webhook
    service.raises = RuntimeError("AI упал")

    with caplog.at_level(logging.ERROR):
        resp = await client.post("/webhook", json=_payload())

    assert resp.status == 200
    assert whatsapp.text_calls == []
    assert any(record.levelno >= logging.ERROR for record in caplog.records)
    assert "AI упал" in caplog.text


async def test_same_user_messages_are_serialized(tmp_path):
    settings = _settings()
    service = FakeConversationService(delay=0.05)
    whatsapp = FakeWhatsAppClient()
    app = create_whatsapp_app(settings, service, whatsapp)

    async with TestClient(TestServer(app)) as client:
        await asyncio.gather(
            client.post("/webhook", json=_payload(text="Первый", message_id="w1")),
            client.post("/webhook", json=_payload(text="Второй", message_id="w2")),
        )

    assert len(service.calls) == 2
    assert service.max_active == 1


async def test_phone_spellings_share_user_and_reply_is_digits_only(tmp_path):
    settings = _settings()
    service = FakeConversationService(delay=0.05)
    whatsapp = FakeWhatsAppClient()
    app = create_whatsapp_app(settings, service, whatsapp)

    async with TestClient(TestServer(app)) as client:
        await asyncio.gather(
            client.post(
                "/webhook",
                json=_payload(
                    text="Первый", sender="77001234567", message_id="w1"
                ),
            ),
            client.post(
                "/webhook",
                json=_payload(
                    text="Второй", sender="+77001234567", message_id="w2"
                ),
            ),
        )

    # Оба написания номера — один пользователь и один замок.
    assert {call["user_id"] for call in service.calls} == {
        CANONICAL_SENDER
    }
    assert service.max_active == 1
    # Ответ уходит только цифрами: Graph API не принимает ведущий "+".
    assert {to for to, _ in whatsapp.text_calls} == {SENDER}
