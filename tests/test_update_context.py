"""Проверяет, что update_id доходит до обработчика."""

from datetime import datetime, timezone

from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User

from bot.middlewares.update_context import UpdateContextMiddleware


async def test_update_id_reaches_handler():
    bot = Bot(token="42:TEST")
    try:
        dispatcher = Dispatcher()
        dispatcher.update.outer_middleware(UpdateContextMiddleware())
        seen = {}

        @dispatcher.message()
        async def handler(message: Message, update_id: int):
            seen["update_id"] = update_id

        update = Update(
            update_id=777,
            message=Message(
                message_id=1,
                date=datetime.now(timezone.utc),
                chat=Chat(id=1, type="private"),
                from_user=User(id=1, is_bot=False, first_name="Test"),
                text="привет",
            ),
        )

        await dispatcher.feed_update(bot, update)

        assert seen == {"update_id": 777}
    finally:
        await bot.session.close()
