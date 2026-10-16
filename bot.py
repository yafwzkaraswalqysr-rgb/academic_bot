from __future__ import annotations
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from database import init_db, close_db
from utils.logging import setup_logging
from handlers.start import router as start_router
from handlers.search import router as search_router
from handlers.callbacks import router as callbacks_router
from handlers.admin import router as admin_router
from handlers.library import router as library_router
from handlers.study import router as study_router
from handlers.graduation import router as graduation_router
from handlers.files import router as files_router
from handlers.voice_image import router as voice_router
from handlers.ai_tutor import router as ai_tutor_router
from handlers.account import router as account_router
from handlers.school import router as school_router
from handlers.university import router as university_router
from handlers.admin_extra import router as admin_extra_router
from sources.telegram import telegram_source
from workers.background import background_tasks
from utils.admin_access import bootstrap_admins


async def main():
    setup_logging()
    log = logging.getLogger(__name__)

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN غير مضبوط في متغيرات البيئة.")

    await init_db()
    await bootstrap_admins()
    bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Admin is last so its generic custom-button text handler cannot swallow
    # normal user messages handled by search/study/account/etc.
    routers = [
        start_router,
        search_router,
        callbacks_router,
        library_router,
        study_router,
        graduation_router,
        files_router,
        voice_router,
        ai_tutor_router,
        account_router,
        school_router,
        university_router,
        admin_router,
        admin_extra_router,
    ]
    for r in routers:
        dp.include_router(r)

    log.info(
        "Registered routers: %s",
        ", ".join(getattr(r, "name", "<unnamed>") or "<unnamed>" for r in routers),
    )

    bg = asyncio.create_task(background_tasks())
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        log.info("Bot started")
        await dp.start_polling(bot)
    finally:
        bg.cancel()
        try:
            await bg
        except asyncio.CancelledError:
            pass
        await telegram_source.stop()
        await bot.session.close()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
