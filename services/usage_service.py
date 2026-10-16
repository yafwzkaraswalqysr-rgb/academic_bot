from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, select
from database import async_session_maker
from models import UsageEvent, User


def _range_start(range_key: str):
    now = datetime.now(timezone.utc)
    if range_key == 'today':
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if range_key == 'yesterday':
        return now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    if range_key == '7d':
        return now - timedelta(days=7)
    if range_key == '30d':
        return now - timedelta(days=30)
    if range_key == 'month':
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if range_key == 'prev_month':
        first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return (first - timedelta(days=1)).replace(day=1)
    return None


async def record_event(telegram_id: int, event_type: str, *, user_id: int | None = None, metadata: dict | None = None):
    try:
        async with async_session_maker() as s:
            if user_id is None:
                u = await s.scalar(select(User.id).where(User.telegram_id == telegram_id))
                user_id = int(u) if u else None
            s.add(UsageEvent(
                user_id=user_id,
                telegram_id=int(telegram_id),
                event_type=str(event_type)[:80],
                metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
            ))
            await s.commit()
    except Exception:
        # Usage analytics must never break the user-facing feature.
        return


async def event_counts(range_key: str = 'all'):
    start = _range_start(range_key)
    async with async_session_maker() as s:
        stmt = select(UsageEvent.event_type, func.count(UsageEvent.id)).group_by(UsageEvent.event_type)
        if range_key == 'yesterday':
            end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            stmt = stmt.where(UsageEvent.created_at >= start, UsageEvent.created_at < end)
        elif start is not None:
            stmt = stmt.where(UsageEvent.created_at >= start)
        rows = (await s.execute(stmt)).all()
        return {str(k): int(v) for k, v in rows}


async def dashboard(range_key: str = 'all'):
    counts = await event_counts(range_key)
    async with async_session_maker() as s:
        total_users = await s.scalar(select(func.count()).select_from(User)) or 0
        new_users = await s.scalar(select(func.count()).select_from(User).where(User.first_seen >= (_range_start(range_key) or datetime.min.replace(tzinfo=timezone.utc)))) or 0
        active_start = _range_start(range_key)
        if active_start is None:
            active_start = datetime.now(timezone.utc) - timedelta(days=7)
        active_users = await s.scalar(select(func.count()).select_from(User).where(User.last_active >= active_start)) or 0
        return {
            'users': int(total_users),
            'active_users': int(active_users),
            'new_users': int(new_users),
            'searches': counts.get('search', 0),
            'downloads': counts.get('download', 0),
            'opens': counts.get('open_file', 0),
            'questions': counts.get('question', 0),
            'exams': counts.get('exam', 0),
            'explanations': counts.get('explanation', 0),
            'summaries': counts.get('summary', 0),
            'sources': counts.get('university_sources', 0),
            'graduation_projects': counts.get('graduation', 0),
            'books_added': counts.get('book_added', 0),
            'files_added': counts.get('file_added', 0),
            'channels_added': counts.get('channel_added', 0),
        }
