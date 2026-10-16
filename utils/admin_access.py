from __future__ import annotations
from sqlalchemy import select
from database import async_session_maker
from models import AdminUser
from config import OWNER_ID, ADMIN_IDS

_CACHE: set[int] = {int(x) for x in ADMIN_IDS if x}
if OWNER_ID:
    _CACHE.add(int(OWNER_ID))


def is_owner(uid: int) -> bool:
    return bool(OWNER_ID and int(uid) == int(OWNER_ID))


def is_admin(uid: int) -> bool:
    return int(uid) in _CACHE or is_owner(uid)


async def bootstrap_admins():
    global _CACHE
    async with async_session_maker() as s:
        rows = (await s.execute(select(AdminUser.telegram_id))).scalars().all()
    _CACHE = {int(x) for x in rows}
    if OWNER_ID:
        _CACHE.add(int(OWNER_ID))

async def add_admin(uid: int, added_by: int):
    global _CACHE
    uid = int(uid)
    if is_owner(uid):
        return False, 'المالك موجود أصلًا.'
    async with async_session_maker() as s:
        row = await s.scalar(select(AdminUser).where(AdminUser.telegram_id == uid))
        if row:
            return False, 'هذا المستخدم مدير بالفعل.'
        s.add(AdminUser(telegram_id=uid, is_owner=False, added_by=int(added_by)))
        await s.commit()
    _CACHE.add(uid)
    return True, 'تمت إضافة المدير.'

async def remove_admin(uid: int):
    global _CACHE
    uid = int(uid)
    if is_owner(uid):
        return False, 'لا يمكن إزالة المالك من داخل النظام.'
    async with async_session_maker() as s:
        row = await s.scalar(select(AdminUser).where(AdminUser.telegram_id == uid))
        if not row:
            return False, 'المدير غير موجود.'
        await s.delete(row)
        await s.commit()
    _CACHE.discard(uid)
    return True, 'تمت إزالة المدير.'

async def list_admins():
    async with async_session_maker() as s:
        return (await s.execute(select(AdminUser).order_by(AdminUser.is_owner.desc(), AdminUser.id))).scalars().all()
