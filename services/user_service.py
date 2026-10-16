from datetime import datetime,timezone
from sqlalchemy import select
from database import async_session_maker
from models import User
async def get_or_create_user(tg_id,username=None,full_name=None):
    async with async_session_maker() as s:
        u=(await s.execute(select(User).where(User.telegram_id==tg_id))).scalar_one_or_none()
        now=datetime.now(timezone.utc)
        if not u:
            u=User(telegram_id=tg_id,username=username,full_name=full_name); s.add(u)
        else:
            u.username=username; u.full_name=full_name; u.last_active=now
        await s.commit(); await s.refresh(u); return u
async def get_user_by_telegram_id(tg_id):
    async with async_session_maker() as s:return (await s.execute(select(User).where(User.telegram_id==tg_id))).scalar_one_or_none()
async def update_user_profile(user_id,**kwargs):
    async with async_session_maker() as s:
        u=await s.get(User,user_id)
        if u:
            for k,v in kwargs.items():
                if hasattr(u,k):setattr(u,k,v)
            await s.commit(); await s.refresh(u)
        return u
