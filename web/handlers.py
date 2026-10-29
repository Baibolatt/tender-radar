"""HTTP-хендлеры панели заказов флориста."""

import asyncio
from pathlib import Path

from aiohttp import web

from db.repositories import OrderRepository, StoreRepository
from web import statuses
from web.auth import (
    SESSION_COOKIE,
    new_token,
    password_matches,
    start_session,
)
from web.keys import DB, SESSIONS, SETTINGS

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Ключи JSON заказа, которые видит фронтенд. Служебные поля скрыты.
ORDER_FIELDS = (
    "id",
    "public_number",
    "client_name",
    "phone",
    "address",
    "delivery_date",
    "delivery_time",
    "bouquet_name",
    "price_kzt",
    "card_text",
    "comment",
    "status",
    "created_at",
    "is_gift",
    "recipient_name",
    "recipient_phone",
)


def order_to_json(row: dict) -> dict:
    """Оставляет только ключи контракта."""
    return {key: row.get(key) for key in ORDER_FIELDS}


async def _store_id(app: web.Application) -> int:
    """Находит магазин из настроек или отдаёт 500."""
    store = await StoreRepository(app[DB]).get_by_code(
        app[SETTINGS].store_code
    )
    if store is None:
        raise web.HTTPInternalServerError()
    return int(store["id"])


async def _read_json(request: web.Request) -> dict:
    """Читает JSON-тело, не падая на пустом или битом запросе."""
    try:
        data = await request.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def index(request: web.Request) -> web.FileResponse:
    """Отдаёт страницу панели."""
    return web.FileResponse(STATIC_DIR / "index.html")


async def login(request: web.Request) -> web.Response:
    """Проверяет пароль и выдаёт cookie сессии."""
    data = await _read_json(request)
    provided = str(data.get("password", ""))
    settings = request.app[SETTINGS]

    if not password_matches(settings, provided):
        return web.json_response({"error": "неверный пароль"}, status=401)

    token = new_token()
    start_session(request.app, token)
    response = web.json_response({"ok": True})
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="Lax"
    )
    return response


async def logout(request: web.Request) -> web.Response:
    """Удаляет сессию из памяти."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        request.app[SESSIONS].pop(token, None)
    return web.json_response({"ok": True})


async def me(request: web.Request) -> web.Response:
    """Сюда доходят только авторизованные: проверку делает middleware."""
    return web.json_response({"authenticated": True})


async def orders(request: web.Request) -> web.Response:
    """Список заказов с фильтром по статусу и поиском."""
    store_id = await _store_id(request.app)
    status = request.query.get("status") or "all"
    query = request.query.get("q") or ""

    rows = await OrderRepository(request.app[DB]).list_orders(
        store_id,
        status="all" if status == "all" else status,
        search=query,
    )
    return web.json_response({"orders": [order_to_json(row) for row in rows]})


async def set_order_status(request: web.Request) -> web.Response:
    """Меняет статус заказа."""
    try:
        order_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "заказ не найден"}, status=404)

    data = await _read_json(request)
    new_status = data.get("status")

    if not statuses.is_valid(new_status):
        return web.json_response({"error": "неизвестный статус"}, status=400)

    row = await OrderRepository(request.app[DB]).set_status(
        order_id, new_status
    )
    if row is None:
        return web.json_response({"error": "заказ не найден"}, status=404)

    return web.json_response({"order": order_to_json(row)})


async def statuses_list(request: web.Request) -> web.Response:
    """Список статусов для кнопок панели."""
    return web.json_response({"statuses": statuses.STATUSES})


async def _orders_fingerprint(app: web.Application, store_id: int) -> tuple:
    """Отпечаток состояния заказов магазина для SSE."""
    cursor = await app[DB].connection.execute(
        """
        SELECT COUNT(*) AS total,
               COALESCE(MAX(id), 0) AS max_id,
               COALESCE(MAX(updated_at), '') AS max_updated
        FROM orders
        WHERE store_id = ?
        """,
        (store_id,),
    )
    row = await cursor.fetchone()
    return (int(row["total"]), int(row["max_id"]), row["max_updated"])


async def events(request: web.Request) -> web.StreamResponse:
    """SSE: сообщает панели, что заказы изменились."""
    store_id = await _store_id(request.app)

    response = web.StreamResponse()
    response.headers["Content-Type"] = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    await response.prepare(request)

    last = None
    ticks = 0
    try:
        while True:
            fingerprint = await _orders_fingerprint(request.app, store_id)
            # На первом заходе только запоминаем состояние, без refresh.
            if last is not None and fingerprint != last:
                await response.write(b"data: refresh\n\n")
            last = fingerprint

            ticks += 1
            if ticks % 10 == 0:
                await response.write(b": ping\n\n")

            await asyncio.sleep(1.5)
    except asyncio.CancelledError:
        # Клиент закрыл вкладку или сервер останавливается.
        return response
