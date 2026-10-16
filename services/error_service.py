from __future__ import annotations
import json
import logging
from database import async_session_maker
from models import ErrorLog

logger = logging.getLogger(__name__)

async def log_error(component: str, message: str, *, user_id: int | None = None, level: str = "ERROR") -> None:
    safe_message = str(message or "")[:6000]
    getattr(logger, level.lower(), logger.error)("%s: %s", component, safe_message)
    try:
        async with async_session_maker() as s:
            s.add(ErrorLog(level=level.upper(), component=component[:100], message=safe_message, user_id=user_id))
            await s.commit()
    except Exception:
        # Logging failures must never break the bot.
        logger.debug("Failed to persist ErrorLog", exc_info=True)
