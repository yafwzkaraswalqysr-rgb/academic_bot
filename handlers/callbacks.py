from aiogram import Router,F
from aiogram.types import CallbackQuery,FSInputFile
from aiogram.fsm.context import FSMContext
from sqlalchemy import select,delete
from database import async_session_maker
from models import Result,LibraryItem
from handlers.search import SearchStates,run_search,show_page
from config import SEARCH_SCOPES,DATE_RANGES
from services.ai_service import ai_service
from services.reference_service import generate_reference
from services.user_service import get_user_by_telegram_id
from services.usage_service import record_event
from services.error_service import log_error
from utils.helpers import *
from utils.security import safe_html_escape
router=Router()
@router.callback_query(F.data=='noop')
async def noop(c):await c.answer()
@router.callback_query(F.data=='main_menu')
async def home(c,state):await state.clear();await c.message.answer('🏠 الرئيسية',reply_markup=await get_main_menu_for_user(c.from_user.id));await c.answer()
@router.callback_query(F.data=='cancel')
async def cancel(c,state):await state.clear();await c.message.answer('تم الإلغاء.',reply_markup=await get_main_menu_for_user(c.from_user.id));await c.answer()
@router.callback_query(F.data.startswith('mode_'))
async def mode(c,state):await state.update_data(mode=c.data[5:]);await state.set_state(SearchStates.query);await c.message.edit_text('✍️ اكتب استعلام البحث:');await c.answer()
@router.callback_query(F.data.startswith('scope_'))
async def scope(c,state):
    i=int(c.data[6:]);await state.update_data(scope=SEARCH_SCOPES[i]);await state.set_state(SearchStates.date);await c.message.edit_text('اختر الفترة:',reply_markup=get_date_keyboard());await c.answer()
@router.callback_query(F.data.startswith('date_'))
async def date(c,state):
    i=int(c.data[5:]);v=DATE_RANGES[i];await state.update_data(date=v)
    if v=='كل الفترات':await state.set_state(SearchStates.limit);await c.message.edit_text('كم نتيجة؟',reply_markup=get_limit_keyboard())
    else:await state.set_state(SearchStates.year);await c.message.edit_text('إذا تريد سنة محددة اكتبها، أو اكتب أي شيء للمتابعة:')
    await c.answer()
@router.callback_query(F.data.startswith('limit_'))
async def limit(c,state):
    raw=c.data[6:]
    if raw=='custom':
        await state.set_state(SearchStates.custom_limit)
        await c.message.answer('✏️ اكتب العدد المطلوب، مثل 250. لا يوجد حد اصطناعي 100.')
        return await c.answer()
    try:
        value=int(raw)
    except Exception:
        return await c.answer('عدد غير صالح.',show_alert=True)
    await state.update_data(limit=value);await run_search(c.message,state);await c.answer()
@router.callback_query(F.data.startswith('save_'))
async def save(c):
    rid=int(c.data[5:]);u=await get_user_by_telegram_id(c.from_user.id)
    if not u:return await c.answer('ابدأ بـ /start',show_alert=True)
    async with async_session_maker() as s:
        exists=await s.scalar(select(LibraryItem).where(LibraryItem.user_id==u.id,LibraryItem.result_id==rid))
        if exists:return await c.answer('محفوظ مسبقًا ⭐')
        s.add(LibraryItem(user_id=u.id,result_id=rid));await s.commit()
    await c.answer('تم الحفظ ⭐')
@router.callback_query(F.data.startswith('ref_'))
async def ref(c):
    rid=int(c.data[4:])
    async with async_session_maker() as s:r=await s.get(Result,rid)
    if not r:return await c.answer('النتيجة غير موجودة',show_alert=True)
    await c.message.answer('📖 <b>المرجع APA:</b>\n'+safe_html_escape(generate_reference({'title':r.title,'author':r.author,'year':r.year,'url':r.url},'APA')),parse_mode='HTML');await c.answer()
@router.callback_query(F.data.startswith('ask_'))
async def ask(c):
    rid=int(c.data[4:])
    async with async_session_maker() as s:r=await s.get(Result,rid)
    if not r:return await c.answer('غير موجود',show_alert=True)
    content=f'[المصدر 1]\nالعنوان: {r.title}\nالمؤلف: {r.author}\nالمقتطف: {r.snippet}\nالرابط: {r.url}'
    await c.answer('جارٍ التحليل...');ans=await ai_service.answer_from_sources('اشرح هذه النتيجة باختصار وما أهميتها؟',content);await c.message.answer('🤖 <b>تحليل المصدر</b>\n\n'+safe_html_escape(ans),parse_mode='HTML')
@router.callback_query(F.data.startswith('download_'))
async def download(c):
    rid=int(c.data[9:])
    async with async_session_maker() as s:r=await s.get(Result,rid)
    if not r or not r.is_downloadable or not r.tg_chat or not r.tg_message_id:
        return await c.answer('الملف غير متاح للتنزيل.',show_alert=True)
    from sources.telegram import telegram_source
    from config import TEMP_DIR, MAX_FILE_SIZE
    import os
    await c.answer('📥 جارٍ تجهيز ملف PDF...')
    path=None
    try:
        if not await telegram_source.start():
            raise RuntimeError('مصدر Telegram غير متصل أو الجلسة غير مصادق عليها.')
        msg=await telegram_source.client.get_messages(r.tg_chat,ids=r.tg_message_id)
        if not msg or not msg.document:
            raise RuntimeError('الرسالة لا تحتوي ملفًا.')
        file_name=getattr(getattr(msg,'file',None),'name',None) or 'academic_source.pdf'
        path=await telegram_source.client.download_media(msg,file=str(TEMP_DIR))
        if not path or not os.path.exists(path):
            raise RuntimeError('تعذر تنزيل الملف من Telegram.')
        size=os.path.getsize(path)
        if size > MAX_FILE_SIZE:
            raise RuntimeError(f'حجم الملف {size/1024/1024:.1f}MB ويتجاوز الحد {MAX_FILE_SIZE/1024/1024:.0f}MB.')
        await c.message.answer_document(FSInputFile(path,filename=file_name),caption=truncate(r.title or file_name,900))
        u = await get_user_by_telegram_id(c.from_user.id)
        await record_event(c.from_user.id, 'download', user_id=(u.id if u else None), metadata={'result_id': rid, 'title': (r.title or '')[:200]})
    except Exception as e:
        await log_error('result.download', str(e), user_id=None)
        await c.message.answer('❌ تعذر إرسال الملف حاليًا. تم تسجيل الخطأ للمطور.')
    finally:
        if path:
            try: os.remove(path)
            except Exception: pass

@router.callback_query(F.data.startswith('page_'))
async def page_callback(c, state):
    try: page=int(c.data[5:])
    except ValueError: return await c.answer('صفحة غير صالحة.',show_alert=True)
    data=await state.get_data(); ids=data.get('result_ids') or []
    if not ids:return await c.answer('انتهت جلسة البحث، أعد البحث من جديد.',show_alert=True)
    await state.update_data(page=page)
    await c.answer()
    await show_page(c.message,ids,page)
