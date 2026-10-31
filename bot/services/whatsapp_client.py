"""Клиент WhatsApp Cloud API: отправка текста и кнопочных сообщений.

Работает через Graph API, токен берётся из настроек во время выполнения и
никогда не пишется в код. Клиент не скрывает ошибки: 401/403, 429, сетевые
сбои и прочие ответы API превращаются в понятные исключения для логов.
"""

import logging

import httpx

from config import Settings

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v21.0"
DEFAULT_TIMEOUT_SECONDS = 10.0
CONNECT_TIMEOUT_SECONDS = 5.0

# Ограничения WhatsApp Cloud API.
MAX_TEXT_LENGTH = 4096
MAX_INTERACTIVE_BODY_LENGTH = 1024
MAX_BUTTONS = 3
MAX_BUTTON_TITLE_LENGTH = 20

# Обрезка тела ошибки API для логов и исключений.
ERROR_BODY_PREVIEW = 300


class WhatsAppError(Exception):
    """Базовая ошибка клиента WhatsApp."""


class WhatsAppConfigurationError(WhatsAppError):
    """Не заданы токен или phone_number_id."""


class WhatsAppAuthError(WhatsAppError):
    """Graph API отклонил токен (HTTP 401/403)."""


class WhatsAppRateLimitError(WhatsAppError):
    """Graph API ограничил частоту запросов (HTTP 429)."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class WhatsAppUnavailable(WhatsAppError):
    """Сетевая ошибка или таймаут при обращении к Graph API."""


class WhatsAppAPIError(WhatsAppError):
    """Graph API вернул неожиданный статус или невалидный JSON."""

    def __init__(
        self, message: str, status_code: int | None = None, body: str = ""
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class WhatsAppClient:
    """Отправляет сообщения клиенту через WhatsApp Cloud API."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.token = settings.whatsapp_token.get_secret_value()
        self.phone_number_id = settings.whatsapp_phone_number_id
        self.base_url = (
            f"https://graph.facebook.com/{GRAPH_API_VERSION}"
            f"/{self.phone_number_id}/messages"
        )
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout_seconds, connect=CONNECT_TIMEOUT_SECONDS
            )
        )

    async def close(self) -> None:
        """Закрывает HTTP-клиент, если он создан внутри WhatsAppClient."""
        if self._owns_http_client:
            await self._http.aclose()

    async def send_text(self, to: str, text: str) -> dict:
        """Отправляет обычное текстовое сообщение и возвращает ответ API."""
        text = self._prepare_text(text, MAX_TEXT_LENGTH, to)
        if not text:
            return {}

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        return await self._post(payload)

    async def send_interactive(
        self, to: str, text: str, buttons: list[dict]
    ) -> dict:
        """Отправляет текст с кнопками быстрого ответа (до 3 штук)."""
        text = self._prepare_text(
            text, MAX_INTERACTIVE_BODY_LENGTH, to, kind="interactive"
        )
        if not text:
            return {}

        prepared = self._prepare_buttons(buttons)
        if not prepared:
            # Без валидных кнопок интерактивное сообщение не отправить,
            # поэтому деградируем до обычного текста.
            logger.warning(
                "Нет валидных кнопок для WhatsApp %s — отправляю текст", to
            )
            return await self.send_text(to, text)

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": text},
                "action": {"buttons": prepared},
            },
        }
        return await self._post(payload)

    async def _post(self, payload: dict) -> dict:
        """Выполняет POST и переводит статусы API в типизированные ошибки."""
        self._ensure_configured()

        try:
            response = await self._http.post(
                self.base_url, headers=self._headers(), json=payload
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning(
                "WhatsApp API недоступен (%s): %s",
                exc.__class__.__name__,
                exc,
            )
            raise WhatsAppUnavailable(
                f"Сетевая ошибка WhatsApp API: {exc.__class__.__name__}: {exc}"
            ) from exc

        status = response.status_code
        body_preview = response.text[:ERROR_BODY_PREVIEW]

        if status in (401, 403):
            logger.error(
                "WhatsApp API отклонил токен: HTTP %s: %s",
                status,
                body_preview,
            )
            raise WhatsAppAuthError(
                f"WhatsApp API отклонил токен: HTTP {status}: {body_preview}"
            )

        if status == 429:
            retry_after = self._retry_after(response)
            logger.warning(
                "WhatsApp API ограничил запросы: HTTP 429, retry_after=%s: %s",
                retry_after,
                body_preview,
            )
            raise WhatsAppRateLimitError(
                f"WhatsApp API ограничил запросы: HTTP 429: {body_preview}",
                retry_after=retry_after,
            )

        if status >= 400:
            logger.error(
                "WhatsApp API вернул ошибку: HTTP %s: %s",
                status,
                body_preview,
            )
            raise WhatsAppAPIError(
                f"WhatsApp API вернул HTTP {status}: {body_preview}",
                status_code=status,
                body=response.text,
            )

        try:
            data = response.json()
        except ValueError as exc:
            logger.error(
                "WhatsApp API вернул не JSON: HTTP %s: %s",
                status,
                body_preview,
            )
            raise WhatsAppAPIError(
                f"WhatsApp API вернул не JSON: HTTP {status}: {body_preview}",
                status_code=status,
                body=response.text,
            ) from exc

        logger.info(
            "WhatsApp сообщение отправлено: to=%s, status=%s, id=%s",
            payload.get("to"),
            status,
            self._message_id(data),
        )
        return data

    def _ensure_configured(self) -> None:
        if not self.token:
            raise WhatsAppConfigurationError("WHATSAPP_TOKEN не задан")
        if not self.phone_number_id:
            raise WhatsAppConfigurationError(
                "WHATSAPP_PHONE_NUMBER_ID не задан"
            )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _prepare_text(
        text: str, limit: int, to: str, kind: str = "text"
    ) -> str:
        """Пропускает пустой текст и обрезает его до лимита WhatsApp."""
        if not text or not text.strip():
            # Пустой ответ модели не должен ронять обработчик вебхука.
            logger.warning(
                "Пустой %s для WhatsApp %s — отправка пропущена", kind, to
            )
            return ""

        if len(text) > limit:
            logger.warning(
                "Текст WhatsApp обрезан до %d символов (было %d), to=%s",
                limit,
                len(text),
                to,
            )
            return text[:limit]
        return text

    @staticmethod
    def _prepare_buttons(buttons: list[dict]) -> list[dict]:
        """Приводит кнопки к формату Graph API, отбрасывая невалидные."""
        prepared: list[dict] = []
        for button in buttons or []:
            if not isinstance(button, dict):
                continue

            # Уже готовый формат Graph API: {"type": "reply", "reply": {...}}
            reply = button.get("reply")
            if isinstance(reply, dict):
                title = str(reply.get("title") or "").strip()
                button_id = str(reply.get("id") or "").strip()
            else:
                title = str(button.get("title") or "").strip()
                button_id = str(button.get("id") or "").strip()

            if not title or not button_id:
                logger.warning(
                    "Кнопка WhatsApp пропущена: нет id или title: %s", button
                )
                continue

            prepared.append(
                {
                    "type": "reply",
                    "reply": {
                        "id": button_id,
                        "title": title[:MAX_BUTTON_TITLE_LENGTH],
                    },
                }
            )
            if len(prepared) == MAX_BUTTONS:
                break

        return prepared

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def _message_id(data: object) -> str | None:
        """Достаёт id отправленного сообщения для лога, не падая на пустом."""
        if not isinstance(data, dict):
            return None
        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            return None
        first = messages[0]
        if not isinstance(first, dict):
            return None
        message_id = first.get("id")
        return message_id if isinstance(message_id, str) else None
