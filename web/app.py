"""Сборка aiohttp-приложения панели заказов."""

from pathlib import Path

from aiohttp import web

from web import handlers
from web.auth import is_authorized

STATIC_DIR = Path(__file__).resolve().parent / "static"


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Закрывает все пути /api/ без действующей сессии."""
    if request.path.startswith("/api/") and not is_authorized(request):
        return web.json_response({"error": "не авторизован"}, status=401)
    return await handler(request)


def create_app(settings, db) -> web.Application:
    """Создаёт приложение со всеми роутами панели."""
    app = web.Application()
    app["settings"] = settings
    app["db"] = db
    app["sessions"] = {}

    # Middleware добавляется до старта приложения
    app.middlewares.append(auth_middleware)

    app.router.add_post("/login", handlers.login)
    app.router.add_post("/logout", handlers.logout)
    app.router.add_get("/api/me", handlers.me)
    app.router.add_get("/api/orders", handlers.orders)
    app.router.add_post(
        "/api/orders/{id}/status", handlers.set_order_status
    )
    app.router.add_get("/api/statuses", handlers.statuses_list)
    app.router.add_get("/api/events", handlers.events)

    app.router.add_static("/static", path=STATIC_DIR)
    app.router.add_get("/", handlers.index)
    return app
