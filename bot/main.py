import asyncio
import logging
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.exc import OperationalError
from dotenv import load_dotenv

from .bot_handlers import setup_handlers
from .config import load_settings
from .db_manager import Database, init_database

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    load_dotenv()
    settings = load_settings()
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    db = Database(
        user=settings.db_user,
        password=settings.db_password,
        host=settings.db_host,
        db_name=settings.db_name,
    )
    # Подождать БД (актуально для Docker, пока MySQL стартует)
    for attempt in range(1, 21):
        try:
            await init_database(db)
            break
        except OperationalError:
            logging.info("БД недоступна, попытка %s/20 через 3с...", attempt)
            await asyncio.sleep(3)
    else:
        raise RuntimeError("Не удалось подключиться к базе данных после 20 попыток")

    setup_handlers(dp, db, settings)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
