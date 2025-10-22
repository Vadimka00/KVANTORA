import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from .config import config
from .db import init_models
from .handlers import channel as channel_handlers
from .handlers import user as user_handlers

async def main():

    # Создаём таблицы в БД (если ещё нет)
    await init_models()
    print("✅ DB init: tables ensured")

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()

    # ✅ Роутеры должны быть добавлены до старта polling
    dp.include_router(user_handlers.router)      # user — первым
    dp.include_router(channel_handlers.router)   # channel — вторым

    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass