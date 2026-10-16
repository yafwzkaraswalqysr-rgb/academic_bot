from __future__ import annotations
import json
from datetime import datetime, timezone
from sqlalchemy import select
from database import async_session_maker
from models import User, StudySession

async def get_session(telegram_id: int):
    async with async_session_maker() as s:
        u = await s.scalar(select(User).where(User.telegram_id == telegram_id))
        if not u:
            return None
        return await s.scalar(select(StudySession).where(StudySession.user_id == u.id))

async def save_session(telegram_id: int, *, title: str, text: str, section: str = "university", content_id: int | None = None,
                       content_type: str | None = None, mode: str | None = None, context: dict | None = None):
    async with async_session_maker() as s:
        u = await s.scalar(select(User).where(User.telegram_id == telegram_id))
        if not u:
            return None
        row = await s.scalar(select(StudySession).where(StudySession.user_id == u.id))
        if row is None:
            row = StudySession(user_id=u.id, source_title=title[:500], source_text=text, section=section)
            s.add(row)
        row.source_title = title[:500]
        row.source_text = text
        row.section = section
        row.source_content_id = content_id
        row.source_content_type = content_type
        row.current_mode = mode
        row.context_json = json.dumps(context or {}, ensure_ascii=False)
        row.updated_at = datetime.now(timezone.utc)
        await s.commit()
        await s.refresh(row)
        return row

async def update_session_mode(telegram_id: int, mode: str, context: dict | None = None):
    async with async_session_maker() as s:
        u = await s.scalar(select(User).where(User.telegram_id == telegram_id))
        if not u:
            return None
        row = await s.scalar(select(StudySession).where(StudySession.user_id == u.id))
        if not row:
            return None
        row.current_mode = mode
        row.updated_at = datetime.now(timezone.utc)
        if context is not None:
            row.context_json = json.dumps(context, ensure_ascii=False)
        await s.commit()
        return row

async def clear_session(telegram_id: int):
    async with async_session_maker() as s:
        u = await s.scalar(select(User).where(User.telegram_id == telegram_id))
        if not u:
            return
        row = await s.scalar(select(StudySession).where(StudySession.user_id == u.id))
        if row:
            await s.delete(row)
            await s.commit()
