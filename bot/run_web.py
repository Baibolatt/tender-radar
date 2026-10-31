"""Запуск WhatsApp webhook-сервера: python -m bot.run_web.

Процесс отдельный от Telegram-бота: оба используют одну базу, поэтому
SingleInstance здесь не нужен. Вебхук слушает 0.0.0.0:8080, чтобы его было
видно из Codespaces и Meta.
"""

import asyncio
import logging
import sys

from aiohttp import web

from ai.client import AIClient
from bot.logging_setup import setup_logging
from bot.services.conversation_service import ConversationService
from bot.services.whatsapp_client import WhatsAppClient
from bot.whatsapp_webhook import create_whatsapp_app
from config import get_settings
from db.database import Database
from db.repositories import StoreRepository

logger = logging.getLogger(__name__)

WEBHOOK_HOST = "0.0.0.0"
WEBHOOK_PORT = 8080


async def run() -> None:
    """Поднимает вебхук WhatsApp и держит его до Ctrl+C."""
    settings = get_settings()
    setup_logging(settings.log_level)

    if not settings.webhook_verify_token.get_secret_value():
        # Не падаем: локально можно проверять приём сообщений без Meta.
        logger.warning(
            "WEBHOOK_VERIFY_TOKEN не задан — Meta не сможет подтвердить вебхук"
        )

    db = Database(settings.database_path)
    await db.connect()

    ai_client = AIClient(settings)
    whatsapp_client = WhatsAppClient(settings)

    try:
        await db.migrate()
        store = await StoreRepository(db).get_by_code(settings.store_code)
        if store is None:
            raise RuntimeError(
                f"Магазин {settings.store_code} не найден. "
                "Выполните: .venv/bin/python -m db.seed_catalog"
            )

        conversation_service = ConversationService(
            db, settings, ai_client, store
        )
        app = create_whatsapp_app(
            settings, conversation_service, whatsapp_client
        )

        # access_log=None обязателен: стандартный лог пишет строку запроса,
        # а в ней Meta передаёт hub.verify_token при проверке вебхука.
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            site = web.TCPSite(
                runner, host=WEBHOOK_HOST, port=WEBHOOK_PORT
            )
            await site.start()
            logger.info(
                "WhatsApp webhook запущен на %s:%s, магазин=%s",
                WEBHOOK_HOST,
                WEBHOOK_PORT,
                store["code"],
            )
            # Держим сервер, пока процесс не прервут (Ctrl+C).
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
            logger.info("WhatsApp webhook остановлен")
    finally:
        await whatsapp_client.close()
        await ai_client.close()
        await db.close()


def main() -> None:
    """Синхронная обёртка для запуска из консоли."""
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        pass
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)
    except Exception:
        logging.getLogger(__name__).exception(
            "Необработанная ошибка при запуске вебхука"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
