from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, delete

from database import async_session_maker
from models import User, Search, Result, LibraryItem, FollowedTopic, StudentProfile, ErrorLog
from services.user_service import get_user_by_telegram_id
from utils.helpers import get_main_menu_for_user
from utils.security import safe_html_escape
from services.usage_service import record_event

router = Router(name="account")


@router.message(F.text == '👤 حسابي')
async def account(m: Message):
    u = await get_user_by_telegram_id(m.from_user.id)
    if not u:
        return await m.answer('ابدأ بـ /start أولاً.')
    text = (
        f'👤 <b>حسابي</b>\n\n'
        f'الاسم: {safe_html_escape(u.full_name or "-")}\n'
        f'المعرف: @{safe_html_escape(u.username or "-")}\n'
        f'المرحلة: {safe_html_escape(u.study_level or "غير محددة")}\n'
        f'التخصص: {safe_html_escape(u.major or "غير محدد")}\n\n'
        f'🔎 عمليات البحث: {u.search_count or 0}\n'
        f'📥 الملفات: {u.upload_count or 0}'
    )
    await m.answer(text, parse_mode='HTML', reply_markup=await get_main_menu_for_user(m.from_user.id))


@router.message(F.text == '🗑 حذف حسابي')
async def delete_account_request(m: Message, state: FSMContext):
    u = await get_user_by_telegram_id(m.from_user.id)
    if not u:
        return await m.answer('لا يوجد حساب محفوظ. ابدأ بـ /start.', reply_markup=None)
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text='🗑 نعم، احذف حسابي', callback_data='account_delete_confirm'),
            InlineKeyboardButton(text='↩️ إلغاء', callback_data='account_delete_cancel'),
        ]
    ])
    await m.answer(
        '⚠️ <b>تأكيد حذف الحساب</b>\n\n'
        'سيتم حذف بيانات حسابك وبياناتك الدراسية والملفات المحفوظة وسجل البحث المرتبط بالحساب.\n\n'
        'لا يمكن التراجع عن هذه العملية.',
        parse_mode='HTML', reply_markup=kb
    )


@router.callback_query(F.data == 'account_delete_cancel')
async def delete_account_cancel(c: CallbackQuery):
    await c.answer('تم إلغاء حذف الحساب.')
    try:
        await c.message.delete()
    except Exception:
        pass
    await c.message.answer('↩️ لم يتم حذف الحساب.', reply_markup=await get_main_menu_for_user(c.from_user.id))


@router.callback_query(F.data == 'account_delete_confirm')
async def delete_account_confirm(c: CallbackQuery, state: FSMContext):
    tg_id = c.from_user.id
    async with async_session_maker() as s:
        u = await s.scalar(select(User).where(User.telegram_id == tg_id))
        if not u:
            await c.answer('الحساب غير موجود.', show_alert=True)
            return
        uid = u.id
        # Explicitly remove dependent records before the user. This works even
        # when a deployment has SQLite foreign-key enforcement disabled.
        search_ids = list((await s.scalars(select(Search.id).where(Search.user_id == uid))).all())
        if search_ids:
            await s.execute(delete(Result).where(Result.search_id.in_(search_ids)))
            await s.execute(delete(Search).where(Search.id.in_(search_ids)))
        await s.execute(delete(LibraryItem).where(LibraryItem.user_id == uid))
        await s.execute(delete(FollowedTopic).where(FollowedTopic.user_id == uid))
        await s.execute(delete(StudentProfile).where(StudentProfile.user_id == uid))
        await s.execute(delete(ErrorLog).where(ErrorLog.user_id == uid))
        # UsageEvent intentionally has no FK to users; historical analytics survive deletion.
        await s.execute(delete(User).where(User.id == uid))
        await s.commit()

    await state.clear()
    await c.answer('تم حذف الحساب.')
    try:
        await c.message.delete()
    except Exception:
        pass
    # The user is now a new visitor; do not show the authenticated menu.
    await c.message.answer(
        '🗑 <b>تم حذف حسابك بنجاح.</b>\n\n'
        'إذا أردت استخدام البوت من جديد، أرسل /start.',
        parse_mode='HTML'
    )


@router.message(F.text == 'ℹ️ المساعدة')
async def help_msg(m: Message):
    await m.answer(
        'ℹ️ <b>المساعدة</b>\n\n'
        'اختر واجهتك من القائمة. طالب المدرسة يستخدم «🏫 مدرستي»، '
        'والجامعي يستخدم أدوات البحث والدراسة، والدراسة الحرة لها مسار مستقل.\n\n'
        'يمكنك حذف حسابك من «🗑 حذف حسابي».',
        parse_mode='HTML', reply_markup=await get_main_menu_for_user(m.from_user.id)
    )
