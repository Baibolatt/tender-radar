"""Точка входа бота."""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from ai.client import AIClient
from bot.handlers.client import build_client_router
from bot.logging_setup import setup_logging
from bot.middlewares.errors import handle_error
from bot.middlewares.services import ServicesMiddleware
from bot.middlewares.update_context import UpdateContextMiddleware
from bot.services.dialog_service import DialogService
from bot.single_instance import SingleInstance
from bot.services.notifier import Notifier
from config import Settings, get_settings
from db.database import Database
from db.repositories import StoreRepository

logger = logging.getLogger(__name__)


def create_dispatcher(dialog_service: DialogService | None = None) -> Dispatcher:
    """Собирает диспетчер с обработчиками и перехватом ошибок."""
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(UpdateContextMiddleware())
    client_router = build_client_router()
    if dialog_service is not None:
        client_router.message.middleware(ServicesMiddleware(dialog_service))
        client_router.callback_query.middleware(
            ServicesMiddleware(dialog_service)
        )
    dispatcher.include_router(client_router)
    dispatcher.errors.register(handle_error)
    return dispatcher


def create_bot(settings: Settings) -> Bot:
    """Создаёт бота. Без токена работать нельзя."""
    token = settings.bot_token.get_secret_value()
    if not token:
        raise RuntimeError("BOT_TOKEN не задан. Укажите его в файле .env.")
    return Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def run() -> None:
    """Подключает базу, загружает магазин и запускает polling."""
    settings = get_settings()
    setup_logging(settings.log_level)

    # Один экземпляр на базу: второй создавал бы дубли заказов.
    lock = SingleInstance(f"{settings.database_path}.lock")
    lock.acquire()

    db = Database(settings.database_path)
    await db.connect()
    ai_client = AIClient(settings)

    try:
        await db.migrate()
        store = await StoreRepository(db).get_by_code(settings.store_code)
        if store is None:
            raise RuntimeError(
                f"Магазин {settings.store_code} не найден. "
                "Выполните: .venv/bin/python -m db.seed_catalog"
            )

        dialog_service = DialogService(db, settings, ai_client, store)
        bot = create_bot(settings)
        dispatcher = create_dispatcher(dialog_service)

        notifier = Notifier(db, bot)
        stop_event = asyncio.Event()
        notifier_task = asyncio.create_task(notifier.run(stop_event))

        logger.info("Бот запускается, магазин=%s", store["code"])
        try:
            await dispatcher.start_polling(bot)
        finally:
            stop_event.set()
            try:
                await notifier_task
            except asyncio.CancelledError:
                pass
            await bot.session.close()
            logger.info("Бот остановлен")
    finally:
        await ai_client.close()
        await db.close()
        lock.release()


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
        logging.getLogger(__name__).exception("Необработанная ошибка при запуске")
        sys.exit(1)


if __name__ == "__main__":
    main()
