"""Клиент Google Gemini API: таймаут, повторы и проверка JSON-ответа."""

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from pydantic import ValidationError

from ai.schemas import AIResponse
from config import Settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER_SECONDS = 30


class AIError(Exception):
    """Базовая ошибка AI-клиента."""


class AIUnavailable(AIError):
    """Модель недоступна после всех попыток."""


class AIInvalidResponse(AIError):
    """Модель вернула невалидный ответ."""


class AIRequestError(AIError):
    """API отклонил запрос (например, неверная модель)."""


class AIConfigurationError(AIError):
    """Проблема с ключом или настройками API."""


@dataclass(frozen=True)
class AICompletion:
    """Разобранный ответ модели и расход токенов."""

    response: AIResponse
    prompt_tokens: int
    completion_tokens: int
    attempts: int


@dataclass(frozen=True)
class _RawCompletion:
    content: str
    prompt_tokens: int
    completion_tokens: int
    attempts: int
    finish_reason: str | None = None


class AIClient:
    """Вызывает Gemini и не пропускает непроверенные ответы дальше."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.settings = settings
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.ai_timeout_seconds, connect=5.0)
        )
        self._sleep = sleep or asyncio.sleep

    @property
    def endpoint(self) -> str:
        return (
            f"{self.settings.ai_base_url.rstrip('/')}"
            f"/models/{self.settings.ai_model}:generateContent"
        )

    async def close(self) -> None:
        """Закрывает HTTP-клиент, если он создан внутри AIClient."""
        if self._owns_http_client:
            await self._http.aclose()

    async def complete(
        self, system_prompt: str, messages: list[dict[str, str]]
    ) -> AICompletion:
        """Делает запрос к модели, повторяет сбой и один раз исправляет JSON."""
        if not self.settings.ai_api_key.get_secret_value():
            raise AIConfigurationError("AI_API_KEY не задан")

        contents = self._to_contents(messages)

        raw = await self._request(system_prompt, contents)
        try:
            completion = self._parse(raw)
        except AIInvalidResponse:
            logger.warning(
                "Модель вернула невалидный JSON, пробую исправление (попытка %d)",
                raw.attempts,
            )
            repair_messages = [
                *messages,
                {"role": "assistant", "content": raw.content},
                {
                    "role": "user",
                    "content": (
                        "Предыдущий ответ обрезан или не является валидным "
                        "JSON по схеме. Верни только JSON-объект с полями "
                        "reply, handoff, ready, order. Поле reply — не длиннее "
                        "600 символов, не перечисляй весь каталог. "
                        "Без markdown и пояснений."
                    ),
                },
            ]
            repaired = await self._request(
                system_prompt, self._to_contents(repair_messages)
            )
            parsed = self._parse(repaired)
            completion = AICompletion(
                response=parsed.response,
                prompt_tokens=raw.prompt_tokens + parsed.prompt_tokens,
                completion_tokens=raw.completion_tokens + parsed.completion_tokens,
                attempts=raw.attempts + parsed.attempts,
            )

        logger.info(
            "AI ответ: модель=%s, попыток=%d, токены=%d/%d",
            self.settings.ai_model,
            completion.attempts,
            completion.prompt_tokens,
            completion.completion_tokens,
        )
        return completion

    async def _request(
        self, system_prompt: str, contents: list[dict[str, object]]
    ) -> _RawCompletion:
        max_attempts = 1 + max(0, self.settings.ai_max_retries)
        last_error = "неизвестная ошибка"

        for attempt in range(1, max_attempts + 1):
            try:
                response = await self._http.post(
                    self.endpoint,
                    headers=self._headers(),
                    json=self._payload(system_prompt, contents),
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"сетевая ошибка: {exc.__class__.__name__}"
                logger.warning(
                    "AI запрос не удался (попытка %d/%d): %s",
                    attempt,
                    max_attempts,
                    last_error,
                )
                if attempt < max_attempts:
                    await self._sleep(self._delay(attempt))
                    continue
                raise AIUnavailable(last_error) from exc

            if response.status_code == 200:
                return self._parse_raw(response, attempt)

            if response.status_code in RETRYABLE_STATUS_CODES:
                last_error = f"HTTP {response.status_code}"
                logger.warning(
                    "AI API вернул %s (попытка %d/%d)",
                    last_error,
                    attempt,
                    max_attempts,
                )
                if attempt < max_attempts:
                    delay = self._retry_after(response) or self._delay(attempt)
                    await self._sleep(delay)
                    continue
                raise AIUnavailable(last_error)

            if response.status_code in (400, 401, 403):
                raise AIConfigurationError(
                    f"AI API отклонил запрос: HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )

            raise AIRequestError(
                f"AI API вернул HTTP {response.status_code}: {response.text[:200]}"
            )

        raise AIUnavailable(last_error)

    def _payload(
        self, system_prompt: str, contents: list[dict[str, object]]
    ) -> dict[str, object]:
        return {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": self.settings.ai_max_tokens,
                "responseMimeType": "application/json",
            },
        }

    def _headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self.settings.ai_api_key.get_secret_value(),
            "Content-Type": "application/json",
        }

    @staticmethod
    def _to_contents(
        messages: list[dict[str, str]]
    ) -> list[dict[str, object]]:
        """Переводит историю диалога в формат Gemini (user/model)."""
        contents: list[dict[str, object]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content", "")
            if not content.strip():
                continue
            if role == "user":
                gemini_role = "user"
            elif role == "assistant":
                gemini_role = "model"
            else:
                continue
            contents.append({"role": gemini_role, "parts": [{"text": content}]})
        return contents

    def _parse_raw(
        self, response: httpx.Response, attempts: int
    ) -> _RawCompletion:
        try:
            data = response.json()
        except ValueError as exc:
            raise AIInvalidResponse("Ответ AI API не является JSON") from exc

        try:
            candidate = data["candidates"][0]
            parts = (candidate.get("content") or {}).get("parts") or []
            content = "".join(
                part.get("text") or ""
                for part in parts
                if isinstance(part, dict)
            )
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise AIInvalidResponse("В ответе AI API нет текста") from exc

        usage = data.get("usageMetadata") or {}
        finish_reason = candidate.get("finishReason")
        return _RawCompletion(
            content=content,
            prompt_tokens=int(usage.get("promptTokenCount") or 0),
            completion_tokens=int(usage.get("candidatesTokenCount") or 0),
            attempts=attempts,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
        )

    def _parse(self, raw: _RawCompletion) -> AICompletion:
        content = self._strip_code_fences(raw.content)

        # Обрезанный ответ почти всегда даёт битый JSON. Сообщаем причину
        # явно, чтобы в логе не было просто «не JSON».
        if raw.finish_reason == "MAX_TOKENS":
            logger.warning(
                "Ответ модели обрезан по лимиту maxOutputTokens=%d. Сырой ответ: %s",
                self.settings.ai_max_tokens,
                content[:4000],
            )
            raise AIInvalidResponse(
                "Ответ модели обрезан: достигнут лимит maxOutputTokens"
            )

        if not content.strip():
            logger.warning(
                "Модель вернула пустой ответ (finish_reason=%s)",
                raw.finish_reason,
            )
            raise AIInvalidResponse("Модель вернула пустой ответ")

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning(
                "Невалидный JSON модели: %s (строка %d, колонка %d). "
                "Сырой ответ: %s",
                exc.msg,
                exc.lineno,
                exc.colno,
                content[:4000],
            )
            raise AIInvalidResponse(
                f"Модель вернула не JSON: {exc.msg}"
            ) from exc

        try:
            response = AIResponse.model_validate(data)
        except ValidationError as exc:
            logger.warning(
                "Ответ модели не соответствует схеме: %s. Сырой ответ: %s",
                exc.errors()[:3],
                content[:4000],
            )
            raise AIInvalidResponse(
                "Ответ модели не соответствует схеме"
            ) from exc

        return AICompletion(
            response=response,
            prompt_tokens=raw.prompt_tokens,
            completion_tokens=raw.completion_tokens,
            attempts=raw.attempts,
        )

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        text = text.strip()
        if not text.startswith("```"):
            return text

        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            seconds = float(value)
        except ValueError:
            return None
        return max(0.0, min(seconds, MAX_RETRY_AFTER_SECONDS))

    def _delay(self, attempt: int) -> float:
        base = max(0.0, self.settings.ai_retry_base_delay_seconds)
        return base * (2 ** (attempt - 1)) + random.uniform(0, 0.2)
