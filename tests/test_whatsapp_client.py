"""Тесты клиента WhatsApp Cloud API: запрос, обрезка текста и ошибки."""

import json

import httpx
import pytest

from bot.services.whatsapp_client import (
    WhatsAppAPIError,
    WhatsAppAuthError,
    WhatsAppClient,
    WhatsAppConfigurationError,
    WhatsAppRateLimitError,
    WhatsAppUnavailable,
)
from config import Settings

PHONE_NUMBER_ID = "1234567890"
TOKEN = "test-token"
EXPECTED_URL = (
    f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
)


def _settings(**overrides):
    values = {
        "whatsapp_token": TOKEN,
        "whatsapp_phone_number_id": PHONE_NUMBER_ID,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _ok_response():
    return httpx.Response(
        200,
        json={
            "messaging_product": "whatsapp",
            "contacts": [{"input": "77001234567", "wa_id": "77001234567"}],
            "messages": [{"id": "wamid.test"}],
        },
    )


@pytest.fixture
async def http_factory():
    clients: list[httpx.AsyncClient] = []

    def make(handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(client)
        return client

    yield make

    for client in clients:
        await client.aclose()


async def test_send_text_makes_correct_request(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        return _ok_response()

    client = WhatsAppClient(_settings(), http_client=http_factory(handler))

    result = await client.send_text("77001234567", "Здравствуйте! Чем помочь?")

    assert len(calls) == 1
    request = calls[0]
    assert request.method == "POST"
    assert str(request.url) == EXPECTED_URL
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Content-Type"] == "application/json"

    body = json.loads(request.content)
    assert body == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "77001234567",
        "type": "text",
        "text": {"preview_url": False, "body": "Здравствуйте! Чем помочь?"},
    }
    assert result["messages"][0]["id"] == "wamid.test"


async def test_send_text_truncates_to_4096_chars(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        return _ok_response()

    client = WhatsAppClient(_settings(), http_client=http_factory(handler))

    await client.send_text("77001234567", "а" * 5000)

    body = json.loads(calls[0].content)
    assert len(body["text"]["body"]) == 4096


async def test_send_text_skips_empty_text(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        return _ok_response()

    client = WhatsAppClient(_settings(), http_client=http_factory(handler))

    assert await client.send_text("77001234567", "") == {}
    assert await client.send_text("77001234567", "   ") == {}
    assert calls == []


async def test_send_interactive_builds_button_payload(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        return _ok_response()

    client = WhatsAppClient(_settings(), http_client=http_factory(handler))

    await client.send_interactive(
        "77001234567",
        "Выберите действие",
        [
            {"id": "yes", "title": "Да"},
            {"id": "no", "title": "Нет"},
        ],
    )

    request = calls[0]
    assert str(request.url) == EXPECTED_URL
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"

    body = json.loads(request.content)
    assert body["type"] == "interactive"
    assert body["interactive"]["type"] == "button"
    assert body["interactive"]["body"]["text"] == "Выберите действие"
    assert body["interactive"]["action"]["buttons"] == [
        {"type": "reply", "reply": {"id": "yes", "title": "Да"}},
        {"type": "reply", "reply": {"id": "no", "title": "Нет"}},
    ]


async def test_send_interactive_limits_buttons_and_titles(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        return _ok_response()

    client = WhatsAppClient(_settings(), http_client=http_factory(handler))

    await client.send_interactive(
        "77001234567",
        "Выберите",
        [
            {"id": "1", "title": "Очень длинная подпись кнопки"},
            {"id": "2", "title": "Два"},
            {"id": "3", "title": "Три"},
            {"id": "4", "title": "Четыре"},
        ],
    )

    buttons = json.loads(calls[0].content)["interactive"]["action"]["buttons"]
    assert len(buttons) == 3
    assert len(buttons[0]["reply"]["title"]) == 20


async def test_send_interactive_without_buttons_falls_back_to_text(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        return _ok_response()

    client = WhatsAppClient(_settings(), http_client=http_factory(handler))

    await client.send_interactive("77001234567", "Просто текст", [])

    body = json.loads(calls[0].content)
    assert body["type"] == "text"
    assert body["text"]["body"] == "Просто текст"


async def test_send_interactive_truncates_body_to_1024_chars(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        return _ok_response()

    client = WhatsAppClient(_settings(), http_client=http_factory(handler))

    await client.send_interactive(
        "77001234567", "б" * 2000, [{"id": "yes", "title": "Да"}]
    )

    body = json.loads(calls[0].content)
    assert len(body["interactive"]["body"]["text"]) == 1024


async def test_unauthorized_raises_auth_error(http_factory):
    def handler(request):
        return httpx.Response(
            401,
            json={"error": {"message": "Invalid OAuth access token"}},
        )

    client = WhatsAppClient(_settings(), http_client=http_factory(handler))

    with pytest.raises(WhatsAppAuthError, match="401"):
        await client.send_text("77001234567", "Привет")


async def test_rate_limit_raises_rate_limit_error(http_factory):
    def handler(request):
        return httpx.Response(
            429,
            json={"error": {"message": "Rate limit hit"}},
            headers={"Retry-After": "30"},
        )

    client = WhatsAppClient(_settings(), http_client=http_factory(handler))

    with pytest.raises(WhatsAppRateLimitError) as exc_info:
        await client.send_text("77001234567", "Привет")

    assert exc_info.value.retry_after == 30
    assert "429" in str(exc_info.value)


async def test_network_error_raises_unavailable(http_factory):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    client = WhatsAppClient(_settings(), http_client=http_factory(handler))

    with pytest.raises(WhatsAppUnavailable, match="ConnectError"):
        await client.send_text("77001234567", "Привет")


async def test_server_error_raises_api_error_with_details(http_factory):
    def handler(request):
        return httpx.Response(500, text="internal error")

    client = WhatsAppClient(_settings(), http_client=http_factory(handler))

    with pytest.raises(WhatsAppAPIError) as exc_info:
        await client.send_text("77001234567", "Привет")

    assert exc_info.value.status_code == 500
    assert "internal error" in str(exc_info.value)


async def test_missing_token_raises_configuration_error(http_factory):
    def handler(request):  # pragma: no cover - не должен вызываться
        raise AssertionError("Запрос не должен уходить без токена")

    client = WhatsAppClient(
        _settings(whatsapp_token=""), http_client=http_factory(handler)
    )

    with pytest.raises(WhatsAppConfigurationError, match="WHATSAPP_TOKEN"):
        await client.send_text("77001234567", "Привет")


async def test_missing_phone_number_id_raises_configuration_error(http_factory):
    def handler(request):  # pragma: no cover - не должен вызываться
        raise AssertionError("Запрос не должен уходить без phone_number_id")

    client = WhatsAppClient(
        _settings(whatsapp_phone_number_id=""), http_client=http_factory(handler)
    )

    with pytest.raises(WhatsAppConfigurationError, match="WHATSAPP_PHONE_NUMBER_ID"):
        await client.send_text("77001234567", "Привет")
