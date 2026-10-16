from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database import async_session_maker
from models import User


async def get_or_create_user(tg_id, username=None, full_name=None):
    async with async_session_maker() as s:
        now = datetime.now(timezone.utc)
        u = (await s.execute(select(User).where(User.telegram_id == tg_id))).scalar_one_or_none()
        if not u:
            u = User(telegram_id=tg_id, username=username, full_name=full_name, last_active=now)
            s.add(u)
            try:
                await s.flush()
            except IntegrityError:
                # Another update created the same Telegram user concurrently.
                await s.rollback()
                u = (await s.execute(select(User).where(User.telegram_id == tg_id))).scalar_one_or_none()
                if not u:
                    raise
                u.username = username
                u.full_name = full_name
                u.last_active = now
        else:
            u.username = username
            u.full_name = full_name
            u.last_active = now
        await s.commit()
        await s.refresh(u)
        return u


async def get_user_by_telegram_id(tg_id):
    async with async_session_maker() as s:
        return (await s.execute(select(User).where(User.telegram_id == tg_id))).scalar_one_or_none()


async def update_user_profile(user_id, **kwargs):
    async with async_session_maker() as s:
        u = await s.get(User, user_id)
        if u:
            for k, v in kwargs.items():
                if hasattr(u, k):
                    setattr(u, k, v)
            await s.commit()
            await s.refresh(u)
        return u
