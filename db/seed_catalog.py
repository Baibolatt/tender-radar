"""Загрузка тестового каталога из db/catalog_seed.json."""

import asyncio
import json
from pathlib import Path
from typing import Any

from bot.logging_setup import setup_logging
from config import get_settings
from db.database import Database
from db.repositories import BouquetRepository, StoreRepository

CATALOG_PATH = Path(__file__).resolve().parent / "catalog_seed.json"


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    """Читает каталог из JSON-файла."""
    return json.loads(path.read_text(encoding="utf-8"))


async def seed_demo_catalog(
    db: Database,
    florist_chat_id: int | None = None,
    path: Path = CATALOG_PATH,
) -> int:
    """Создаёт магазин и 15 букетов, если каталог ещё пуст.

    Повторный запуск ничего не дублирует и не перезаписывает настоящий каталог.
    """
    catalog = load_catalog(path)
    stores = StoreRepository(db)
    bouquets = BouquetRepository(db)

    store = await stores.get_by_code(catalog["store_code"])
    if store is None:
        store_id = await stores.create(
            catalog["store_code"],
            catalog["store_name"],
            florist_chat_id=florist_chat_id,
        )
    else:
        store_id = int(store["id"])
        if florist_chat_id is not None and store["florist_chat_id"] is None:
            await stores.set_florist_chat_id(store_id, florist_chat_id)

    if await bouquets.count(store_id) == 0:
        await bouquets.create_many(store_id, catalog["bouquets"])

    return store_id


async def main() -> None:
    """Запуск из консоли: python -m db.seed_catalog."""
    settings = get_settings()
    setup_logging(settings.log_level)

    db = Database(settings.database_path)
    await db.connect()
    await db.migrate()
    try:
        store_id = await seed_demo_catalog(
            db, florist_chat_id=settings.florist_chat_id
        )
        count = await BouquetRepository(db).count(store_id)
        print(f"Магазин {store_id}: букетов в каталоге — {count}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
