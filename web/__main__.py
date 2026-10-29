"""Запуск панели заказов: python -m web."""

import asyncio

from aiohttp import web

from config import get_settings
from db.database import Database
from web.app import create_app


async def main() -> None:
    settings = get_settings()
    if not settings.panel_password.get_secret_value():
        raise RuntimeError("PANEL_PASSWORD не задан. Укажите его в файле .env.")

    db = Database(settings.database_path)
    await db.connect()
    try:
        await db.migrate()
        app = create_app(settings, db)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(
            runner, host=settings.panel_host, port=settings.panel_port
        )
        await site.start()
        # Держим сервер, пока процесс не прервут (Ctrl+C).
        await asyncio.Event().wait()
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
