"""Тесты AI-клиента: успех, повторы, ошибки и исправление JSON."""

import json
import logging

import httpx
import pytest

from ai.client import (
    AIClient,
    AIConfigurationError,
    AIInvalidResponse,
    AIUnavailable,
)
from ai.prompts import build_system_prompt
from config import Settings

VALID_DATA = {
    "reply": "Записал. Проверьте, пожалуйста, детали заказа.",
    "handoff": False,
    "order": {
        "client_name": "Айгуль",
        "phone": "+77001234567",
        "address": "Тараз, ул. Абая, 10",
        "delivery_date": "2026-10-30",
        "delivery_time": "14:00-16:00",
        "bouquet_id": 1,
        "card_text": "С днём рождения!",
        "comment": None,
    },
}
VALID_JSON = json.dumps(VALID_DATA, ensure_ascii=False)


def _settings(**overrides):
    values = {
        "ai_api_key": "test-key",
        "ai_model": "gemini-3.1-flash-lite",
        "ai_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "ai_max_retries": 2,
        "ai_retry_base_delay_seconds": 0,
        "ai_timeout_seconds": 5,
        "ai_max_tokens": 400,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _completion(
    content: str,
    status: int = 200,
    headers=None,
    finish_reason: str | None = None,
):
    return httpx.Response(
        status,
        json={
            "candidates": [
                {
                    "content": {"parts": [{"text": content}]},
                    "finishReason": finish_reason,
                }
            ],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20},
        },
        headers=headers,
    )


class FakeSleeper:
    def __init__(self):
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


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


async def test_successful_completion(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        return _completion(VALID_JSON)

    sleeper = FakeSleeper()
    ai = AIClient(_settings(), http_client=http_factory(handler), sleep=sleeper)

    completion = await ai.complete(
        "системный промпт", [{"role": "user", "content": "Привет"}]
    )

    assert completion.response.reply == VALID_DATA["reply"]
    assert completion.response.order.bouquet_id == 1
    assert completion.response.order.client_name == "Айгуль"
    assert completion.prompt_tokens == 10
    assert completion.completion_tokens == 20
    assert completion.attempts == 1
    assert len(calls) == 1
    assert sleeper.delays == []

    body = json.loads(calls[0].content)
    assert str(calls[0].url) == (
        "https://generativelanguage.googleapis.com/v1beta"
        "/models/gemini-3.1-flash-lite:generateContent"
    )
    assert calls[0].headers["x-goog-api-key"] == "test-key"
    assert body["systemInstruction"] == {
        "parts": [{"text": "системный промпт"}]
    }
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "Привет"}]}
    ]
    assert body["generationConfig"]["maxOutputTokens"] == 400
    assert body["generationConfig"]["responseMimeType"] == "application/json"


async def test_retries_on_server_error(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(500, text="fail")
        return _completion(VALID_JSON)

    sleeper = FakeSleeper()
    ai = AIClient(_settings(), http_client=http_factory(handler), sleep=sleeper)

    completion = await ai.complete("system", [{"role": "user", "content": "Привет"}])

    assert len(calls) == 3
    assert completion.attempts == 3
    assert len(sleeper.delays) == 2


async def test_gives_up_after_all_attempts(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, text="unavailable")

    ai = AIClient(
        _settings(), http_client=http_factory(handler), sleep=FakeSleeper()
    )

    with pytest.raises(AIUnavailable):
        await ai.complete("system", [{"role": "user", "content": "Привет"}])

    assert len(calls) == 3


async def test_does_not_retry_bad_key(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, text="API key not valid")

    ai = AIClient(
        _settings(), http_client=http_factory(handler), sleep=FakeSleeper()
    )

    with pytest.raises(AIConfigurationError):
        await ai.complete("system", [{"role": "user", "content": "Привет"}])

    assert len(calls) == 1


async def test_retries_network_error(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("down", request=request)

    ai = AIClient(
        _settings(), http_client=http_factory(handler), sleep=FakeSleeper()
    )

    with pytest.raises(AIUnavailable):
        await ai.complete("system", [{"role": "user", "content": "Привет"}])

    assert len(calls) == 3


async def test_invalid_json_is_repaired(http_factory):
    calls = []
    responses = ["не json", VALID_JSON]

    def handler(request):
        calls.append(request)
        return _completion(responses.pop(0))

    ai = AIClient(
        _settings(), http_client=http_factory(handler), sleep=FakeSleeper()
    )

    completion = await ai.complete(
        "system", [{"role": "user", "content": "Привет"}]
    )

    assert len(calls) == 2
    assert completion.attempts == 2
    assert completion.prompt_tokens == 20
    assert completion.completion_tokens == 40

    repair_body = json.loads(calls[1].content)
    assert repair_body["contents"][-2] == {
        "role": "model",
        "parts": [{"text": "не json"}],
    }
    assert repair_body["contents"][-1]["role"] == "user"
    assert "JSON" in repair_body["contents"][-1]["parts"][0]["text"]


async def test_invalid_json_twice_raises(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        return _completion("не json")

    ai = AIClient(
        _settings(), http_client=http_factory(handler), sleep=FakeSleeper()
    )

    with pytest.raises(AIInvalidResponse):
        await ai.complete("system", [{"role": "user", "content": "Привет"}])

    assert len(calls) == 2


async def test_truncated_response_is_repaired_with_short_request(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return _completion('{"reply": "Конечно, вот каталог:\\nМини-букет', finish_reason="MAX_TOKENS")
        return _completion(VALID_JSON)

    ai = AIClient(
        _settings(), http_client=http_factory(handler), sleep=FakeSleeper()
    )

    completion = await ai.complete(
        "system", [{"role": "user", "content": "Список букетов"}]
    )

    assert completion.response.reply == VALID_DATA["reply"]
    assert len(calls) == 2
    repair_text = json.loads(calls[1].content)["contents"][-1]["parts"][0]["text"]
    assert "600" in repair_text
    assert "каталог" in repair_text


async def test_truncated_twice_raises_clear_reason(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        return _completion('{"reply": "обрезано', finish_reason="MAX_TOKENS")

    ai = AIClient(
        _settings(), http_client=http_factory(handler), sleep=FakeSleeper()
    )

    with pytest.raises(AIInvalidResponse, match="обрезан"):
        await ai.complete("system", [{"role": "user", "content": "Привет"}])

    assert len(calls) == 2


async def test_empty_content_is_repaired(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return _completion("   ", finish_reason="STOP")
        return _completion(VALID_JSON)

    ai = AIClient(
        _settings(), http_client=http_factory(handler), sleep=FakeSleeper()
    )

    completion = await ai.complete(
        "system", [{"role": "user", "content": "Привет"}]
    )

    assert completion.response.reply == VALID_DATA["reply"]
    assert len(calls) == 2


async def test_invalid_json_logs_raw_model_answer(http_factory, caplog):
    caplog.set_level(logging.WARNING)

    def handler(request):
        return _completion("вот ваш ответ без JSON")

    ai = AIClient(
        _settings(), http_client=http_factory(handler), sleep=FakeSleeper()
    )

    with pytest.raises(AIInvalidResponse):
        await ai.complete("system", [{"role": "user", "content": "Привет"}])

    assert "вот ваш ответ без JSON" in caplog.text


async def test_strips_markdown_fences(http_factory):
    def handler(request):
        return _completion(f"```json\n{VALID_JSON}\n```")

    ai = AIClient(
        _settings(), http_client=http_factory(handler), sleep=FakeSleeper()
    )

    completion = await ai.complete(
        "system", [{"role": "user", "content": "Привет"}]
    )

    assert completion.response.order.phone == "+77001234567"


async def test_missing_api_key_does_not_call_api(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        return _completion(VALID_JSON)

    ai = AIClient(
        _settings(ai_api_key=""),
        http_client=http_factory(handler),
        sleep=FakeSleeper(),
    )

    with pytest.raises(AIConfigurationError):
        await ai.complete("system", [{"role": "user", "content": "Привет"}])

    assert calls == []


async def test_uses_retry_after_header(http_factory):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, text="slow")
        return _completion(VALID_JSON)

    sleeper = FakeSleeper()
    ai = AIClient(_settings(), http_client=http_factory(handler), sleep=sleeper)

    await ai.complete("system", [{"role": "user", "content": "Привет"}])

    assert sleeper.delays == [7.0]


def test_prompt_contains_catalog_and_json_rules():
    prompt = build_system_prompt("Tender", "1. «Розы» — 15000 тг")

    assert "Tender" in prompt
    assert "15000" in prompt
    assert '"handoff"' in prompt
    assert "JSON" in prompt
    assert "Не перечисляй весь каталог" in prompt
    assert "600 символов" in prompt
