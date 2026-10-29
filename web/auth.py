"""Сессии панели флориста: токены в памяти и проверка пароля."""

import secrets
import time

from aiohttp import web

from web.keys import SESSIONS

SESSION_COOKIE = "session"
SESSION_TTL_SECONDS = 86400


def new_token() -> str:
    """Новый случайный токен сессии."""
    return secrets.token_urlsafe(32)


def start_session(app: web.Application, token: str) -> None:
    """Запоминает токен в памяти и срок его действия."""
    app[SESSIONS][token] = time.time() + SESSION_TTL_SECONDS


def is_authorized(request: web.Request) -> bool:
    """Проверяет cookie сессии и заодно убирает просроченные токены."""
    sessions: dict[str, float] = request.app[SESSIONS]
    now = time.time()

    # Удаляем просроченные сессии, чтобы память не росла
    for token in [key for key, expiry in sessions.items() if expiry <= now]:
        sessions.pop(token, None)

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False

    expiry = sessions.get(token)
    return expiry is not None and expiry > now


def password_matches(settings, provided: str) -> bool:
    """Сравнивает пароль за постоянное время, без утечки длины."""
    expected = settings.panel_password.get_secret_value()
    return secrets.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    )
