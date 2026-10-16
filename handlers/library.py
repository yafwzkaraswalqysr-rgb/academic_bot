from aiogram import Router,F
from aiogram.types import Message
from sqlalchemy import select
from database import async_session_maker
from models import LibraryItem,Result,User
from services.user_service import get_user_by_telegram_id
from utils.security import safe_html_escape
router=Router()
@router.message(F.text.in_({'🗂 مكتبتي','⭐ المحفوظات'}))
async def library(m):
 u=await get_user_by_telegram_id(m.from_user.id)
 if not u:return await m.answer('ابدأ بـ /start')
 async with async_session_maker() as s:
  items=(await s.execute(select(LibraryItem,Result).join(Result,Result.id==LibraryItem.result_id).where(LibraryItem.user_id==u.id).order_by(LibraryItem.saved_at.desc()).limit(30))).all()
 if not items:return await m.answer('مكتبتك فارغة حاليًا.')
 await m.answer('🗂 آخر المحفوظات:\n\n'+'\n\n'.join(f'• {safe_html_escape(r.title)}\n  {safe_html_escape(r.url or "بدون رابط")}' for _,r in items)[:4000], parse_mode='HTML')
