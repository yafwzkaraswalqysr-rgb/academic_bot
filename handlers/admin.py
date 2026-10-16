from __future__ import annotations
import asyncio
import re
import secrets
from pathlib import Path
from aiogram import Router,F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message,CallbackQuery,InlineKeyboardMarkup,InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State,StatesGroup
from sqlalchemy import select,func,delete
from database import async_session_maker
from models import TelegramSource,WebSource,User,SchoolContent,SchoolPlacement
from config import ADMIN_IDS,OWNER_ID, SEARCH_TIMEOUT, MAX_STUDY_TEXT_CHARS
from sources.telegram import telegram_source, normalize_telegram_identifier
from utils.security import is_ssrf_safe_url
from urllib.parse import urlparse
from student_system import load_buttons,save_buttons,DEFAULT_BUTTONS
from utils.helpers import get_main_menu_for_user
from utils.admin_access import is_admin, is_owner
from services.content_index import extract_telegram_document
from services.search_engine import search_engine
from services.usage_service import record_event
from services.error_service import log_error
import logging
from utils.security import safe_html_escape
router=Router(name='admin')
log = logging.getLogger(__name__)

def menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🏫 إدارة المدرسة',callback_data='adm_school'),InlineKeyboardButton(text='🎓 إدارة الجامعة',callback_data='adm_university')],
        [InlineKeyboardButton(text='📚 إدارة الكتب',callback_data='adm_books'),InlineKeyboardButton(text='📄 إدارة الملفات',callback_data='sc_list')],
        [InlineKeyboardButton(text='📡 قنوات Telegram',callback_data='adm_tg'),InlineKeyboardButton(text='🌐 مواقع الويب',callback_data='adm_web')],
        [InlineKeyboardButton(text='👥 المستخدمون',callback_data='adm_users_extra'),InlineKeyboardButton(text='📊 الإحصائيات',callback_data='adm_stats_extra')],
        [InlineKeyboardButton(text='👨‍💼 إدارة المدراء',callback_data='adm_admins'),InlineKeyboardButton(text='🔎 إعادة الفهرسة',callback_data='adm_reindex')],
        [InlineKeyboardButton(text='🔘 إدارة الأزرار',callback_data='adm_buttons'),InlineKeyboardButton(text='🤖 حالة الذكاء الاصطناعي',callback_data='adm_ai_status')],
    ])
class A(StatesGroup):
    tg_name=State();tg_user=State();tg_purpose=State();tg_edit=State();web_name=State();web_search=State();web_purpose=State();web_edit=State();button_label=State();button_content=State();button_link=State();school_file=State();school_path=State();school_title=State();school_publish_branch=State();school_edit=State()

@router.message(Command('admin'))
@router.message(F.text=='🛠 لوحة المالك')
async def admin(m):
    if not is_admin(m.from_user.id):return await m.answer('⛔ غير مسموح.')
    await m.answer('🛠 <b>لوحة المالك</b>',parse_mode='HTML',reply_markup=menu())

@router.callback_query(F.data=='adm_buttons')
async def buttons(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    items=load_buttons(); rows=[]
    for x in items:
        key=str(x.get('key',''))
        icon='🟢' if x.get('enabled',True) else '🔴'
        if str(x.get('action','')).startswith('custom:'):
            rows.append([
                InlineKeyboardButton(text=f"{icon} {x.get('label','')}",callback_data=f"btn_toggle:{key}"),
                InlineKeyboardButton(text='🗑',callback_data=f"btn_del:{key}"),
            ])
        else:
            rows.append([InlineKeyboardButton(text=f"{icon} {x.get('label','')}",callback_data=f"btn_toggle:{key}")])
    rows += [[InlineKeyboardButton(text='➕ إضافة زر',callback_data='btn_add')],[InlineKeyboardButton(text='♻️ إعادة الأزرار الأساسية',callback_data='btn_reset')],[InlineKeyboardButton(text='🏠',callback_data='admin_home')]]
    await c.message.answer('🔘 <b>إدارة أزرار القائمة</b>\n🟢 ظاهر / 🔴 مخفي\nاضغط الزر لتبديل ظهوره. الأزرار المخصصة يمكن حذفها.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)); await c.answer()

@router.callback_query(F.data.startswith('btn_toggle:'))
async def btn_toggle(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    key=c.data.split(':',1)[1]; items=load_buttons()
    for x in items:
        if x.get('key')==key:x['enabled']=not x.get('enabled',True)
    save_buttons(items); await c.answer('تم تغيير الحالة'); await buttons(c)

@router.callback_query(F.data.startswith('btn_del:'))
async def btn_del(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    key=c.data.split(':',1)[1]; items=[x for x in load_buttons() if x.get('key')!=key]; save_buttons(items); await c.answer('تم الحذف'); await buttons(c)

@router.callback_query(F.data=='btn_reset')
async def btn_reset(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    save_buttons(DEFAULT_BUTTONS); await c.answer('تمت إعادة الأزرار الأساسية'); await buttons(c)

@router.callback_query(F.data=='btn_add')
async def btn_add(c,state):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    await state.set_state(A.button_label); await c.message.answer('✏️ أرسل اسم الزر كما تريد ظهوره:'); await c.answer()

@router.message(A.button_label,F.text)
async def button_label(m,state):
    if not is_admin(m.from_user.id): return
    label=m.text.strip()[:64]
    if not label:return await m.answer('اسم الزر لا يمكن أن يكون فارغًا.')
    await state.update_data(label=label); await state.set_state(A.button_content); await m.answer('📝 أرسل محتوى الزر الذي سيظهر للمستخدم، أو أرسل ملف PDF مباشرة:')

@router.message(A.button_content,F.text)
async def button_content(m,state):
    if not is_admin(m.from_user.id): return
    await state.update_data(content=m.text.strip()[:3500]); await state.set_state(A.button_link); await m.answer('🔗 أرسل الرابط، أو اكتب - إذا لا يوجد رابط:')

@router.message(A.button_link,F.text)
async def button_link(m,state):
    if not is_admin(m.from_user.id): return
    link=m.text.strip()
    if link!='-' and not re.match(r'^https?://[^\s]+$',link,re.I):return await m.answer('❌ أرسل رابط http/https صحيح أو -')
    d=await state.get_data(); items=load_buttons(); key='custom_'+secrets.token_hex(6)
    items.append({'key':key,'label':d['label'],'enabled':True,'order':max([int(x.get('order',0)) for x in items] or [0])+10,'action':'custom:'+d['content'],'link':None if link=='-' else link}); save_buttons(items); await state.clear(); await m.answer('✅ تمت إضافة الزر.',reply_markup=menu())

@router.message(StateFilter(None), F.text)
async def custom_button_dispatch(m):
    if not m.text:return
    for x in load_buttons():
        if x.get('label')==m.text and str(x.get('action','')).startswith('custom:'):
            text=x['action'][7:]
            if x.get('link'):text += f"\n\n🔗 {x['link']}"
            return await m.answer(text,reply_markup=None)

@router.callback_query(F.data=='adm_stats')
async def stats(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    from handlers.admin_extra import adm_stats_extra
    await adm_stats_extra(c)

@router.callback_query(F.data=='adm_users')
async def users(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    async with async_session_maker() as s:us=(await s.execute(select(User).order_by(User.last_active.desc()).limit(50))).scalars().all()
    text='👥 المستخدمون\n\n'+'\n'.join(f'{u.telegram_id} | @{u.username or "-"} | بحث:{u.search_count or 0}' for u in us) or 'لا يوجد مستخدمون.'
    await c.message.answer(text[:4000]);await c.answer()

@router.callback_query(F.data=='adm_tg')
async def tg(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    async with async_session_maker() as s:items=(await s.execute(select(TelegramSource).order_by(TelegramSource.priority,TelegramSource.id))).scalars().all()
    purpose_labels={'university_search':'📚 بحث الجامعة','graduation':'🎓 مشروع التخرج','both':'🔎 البحث + مشروع التخرج','general':'🔎 عام','books':'📚 كتب'}
    rows=[[InlineKeyboardButton(text=f"{'🟢' if x.enabled else '🔴'} {x.name} · {purpose_labels.get((x.source_type or '').strip(),'📚 بحث الجامعة')}",callback_data=f'tg_toggle_{x.id}'),InlineKeyboardButton(text='✏️',callback_data=f'tg_edit_{x.id}'),InlineKeyboardButton(text='🗑',callback_data=f'tg_del_{x.id}')] for x in items]
    rows += [[InlineKeyboardButton(text='➕ إضافة قناة',callback_data='addtg')],[InlineKeyboardButton(text='🏠',callback_data='admin_home')]]
    await c.message.answer('📡 <b>مصادر Telegram</b>',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows));await c.answer()
@router.callback_query(F.data=='admin_home')
async def ah(c, state):
    if not is_admin(c.from_user.id): return await c.answer('غير مسموح',show_alert=True)
    await state.clear()
    await c.message.answer('🛠 لوحة المالك',reply_markup=menu());await c.answer()
@router.callback_query(F.data.startswith('tg_toggle_'))
async def tgt(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    async with async_session_maker() as s:
        x=await s.get(TelegramSource,int(c.data[10:]));
        if x:x.enabled=not x.enabled;await s.commit()
    search_engine.invalidate(); await c.answer('تم تغيير الحالة');await tg(c)
@router.callback_query(F.data.startswith('tg_edit_'))
async def tg_edit_start(c,state):
    if not is_admin(c.from_user.id): return await c.answer('غير مسموح',show_alert=True)
    sid=int(c.data.split('_')[-1])
    async with async_session_maker() as s: x=await s.get(TelegramSource,sid)
    if not x:return await c.answer('المصدر غير موجود',show_alert=True)
    await state.set_state(A.tg_edit); await state.update_data(tg_id=sid)
    await c.message.answer(
        f'✏️ <b>تعديل مصدر Telegram</b>\n\nالاسم الحالي: {safe_html_escape(x.name)}\nالمعرف الحالي: <code>{safe_html_escape(x.username or "-")}</code>\n\n'
        'أرسل: الاسم | @username أو username أو رابط t.me أو Chat ID', parse_mode='HTML')
    await c.answer()

@router.message(A.tg_edit,F.text)
async def tg_edit_save(m,state):
    if not is_admin(m.from_user.id): return
    parts=[x.strip() for x in m.text.split('|',1)]
    if len(parts)!=2 or not parts[0] or not parts[1]: return await m.answer('❌ الصيغة: الاسم | @username أو الرابط أو Chat ID')
    entity,canonical=normalize_telegram_identifier(parts[1])
    if entity is None:return await m.answer('❌ المعرف غير صالح.')
    name=parts[0][:255]; ident=str(entity)[:255]
    url=canonical or (f'https://t.me/{entity}' if isinstance(entity,str) and not entity.startswith('-') else parts[1])
    d=await state.get_data(); sid=int(d.get('tg_id',0))
    async with async_session_maker() as s:
        x=await s.get(TelegramSource,sid)
        if not x:return await m.answer('المصدر غير موجود.')
        # Avoid the unique username collision with another source.
        from sqlalchemy import select as sa_select
        dup=await s.scalar(sa_select(TelegramSource).where(TelegramSource.username==ident,TelegramSource.id!=sid))
        if dup:return await m.answer('❌ هذا المعرف مستخدم من مصدر Telegram آخر.')
        x.name=name; x.username=ident; x.url=url; await s.commit()
    search_engine.invalidate(); await state.clear(); await m.answer('✅ تم تعديل مصدر Telegram ومسح كاش البحث.',reply_markup=menu())

@router.callback_query(F.data.startswith('tg_del_'))
async def tgd(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    async with async_session_maker() as s:await s.execute(delete(TelegramSource).where(TelegramSource.id==int(c.data[7:])));await s.commit()
    search_engine.invalidate(); await c.answer('تم الحذف');await tg(c)
@router.callback_query(F.data=='addtg')
async def addtg(c,state):
    if not is_admin(c.from_user.id): return await c.answer('غير مسموح',show_alert=True)
    await state.set_state(A.tg_name);await c.message.answer('اسم القناة:');await c.answer()
@router.message(A.tg_name)
async def tgn(m,state):
    if not is_admin(m.from_user.id): return
    await state.update_data(name=m.text.strip());await state.set_state(A.tg_user);await m.answer('username القناة (مثال @example):')
@router.message(A.tg_user)
async def tgu(m,state):
    if not is_admin(m.from_user.id):return
    raw=m.text.strip()
    entity, canonical=normalize_telegram_identifier(raw)
    if entity is None:
        return await m.answer('❌ أدخل @username أو username أو رابط Telegram عام أو Chat ID صالح.')
    d=await state.get_data(); username=str(entity)
    url=canonical or (f'https://t.me/{entity}' if isinstance(entity,str) and entity and not entity.startswith('-') else raw)
    await state.update_data(tg_username=username[:255], tg_url=url, tg_entity_ref=entity)
    await state.set_state(A.tg_purpose)
    await m.answer('📌 هذا المصدر مخصص لمن؟', reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📚 الجامعة — البحث',callback_data='tg_purpose:university_search')],
        [InlineKeyboardButton(text='🎓 الجامعة — مشروع التخرج',callback_data='tg_purpose:graduation')],
        [InlineKeyboardButton(text='🔎 الاثنان',callback_data='tg_purpose:both')]
    ]))
@router.callback_query(F.data=='adm_web')
async def web(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    async with async_session_maker() as s:items=(await s.execute(select(WebSource).order_by(WebSource.priority,WebSource.id))).scalars().all()
    purpose_labels={'university_search':'📚 بحث الجامعة','graduation':'🎓 مشروع التخرج','both':'🔎 البحث + مشروع التخرج','general':'🔎 عام','books':'📚 كتب'}
    rows=[[InlineKeyboardButton(text=f"{'🟢' if x.enabled else '🔴'} {x.name} · {purpose_labels.get((x.source_type or '').strip(),'📚 بحث الجامعة')}",callback_data=f'web_toggle_{x.id}'),InlineKeyboardButton(text='✏️',callback_data=f'web_edit_{x.id}'),InlineKeyboardButton(text='🗑',callback_data=f'web_del_{x.id}')] for x in items]
    rows += [[InlineKeyboardButton(text='➕ إضافة موقع',callback_data='addweb')],[InlineKeyboardButton(text='🏠',callback_data='admin_home')]]
    await c.message.answer('🌐 <b>مصادر الويب</b>',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows));await c.answer()
@router.callback_query(F.data.startswith('web_toggle_'))
async def webt(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    async with async_session_maker() as s:
        x=await s.get(WebSource,int(c.data[11:]));
        if x:x.enabled=not x.enabled;await s.commit()
    search_engine.invalidate(); await c.answer('تم');await web(c)
@router.callback_query(F.data.startswith('web_edit_'))
async def web_edit_start(c,state):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    wid=int(c.data.split('_')[-1])
    async with async_session_maker() as s:x=await s.get(WebSource,wid)
    if not x:return await c.answer('الموقع غير موجود',show_alert=True)
    await state.set_state(A.web_edit); await state.update_data(web_id=wid)
    await c.message.answer(f'✏️ <b>تعديل الموقع</b>\n\nالاسم: {safe_html_escape(x.name)}\nالرابط الحالي: <code>{safe_html_escape(x.search_url)}</code>\n\nأرسل: الاسم | رابط البحث، ويجب أن يحتوي {{query}}',parse_mode='HTML'); await c.answer()

@router.message(A.web_edit,F.text)
async def web_edit_save(m,state):
    if not is_admin(m.from_user.id):return
    parts=[x.strip() for x in m.text.split('|',1)]
    if len(parts)!=2 or not parts[0] or '{query}' not in parts[1]:return await m.answer('❌ الصيغة: الاسم | رابط البحث الذي يحتوي {query}')
    url=parts[1]
    test=url.replace('{query}','test')
    if not is_ssrf_safe_url(test):return await m.answer('❌ الرابط غير صالح أو غير آمن.')
    d=await state.get_data(); wid=int(d.get('web_id',0))
    async with async_session_maker() as s:
        x=await s.get(WebSource,wid)
        if not x:return await m.answer('الموقع غير موجود.')
        duplicate=await s.scalar(select(WebSource).where(WebSource.search_url==url, WebSource.id!=wid))
        if duplicate:return await m.answer('❌ هذا رابط البحث مستخدم من مصدر آخر.')
        p=urlparse(test); x.name=parts[0][:255]; x.search_url=url; x.base_url=f'{p.scheme}://{p.netloc}'; await s.commit()
    search_engine.invalidate(); await state.clear(); await m.answer('✅ تم تعديل الموقع ومسح كاش البحث.',reply_markup=menu())

@router.callback_query(F.data.startswith('web_del_'))
async def webd(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    async with async_session_maker() as s:await s.execute(delete(WebSource).where(WebSource.id==int(c.data[8:])));await s.commit()
    search_engine.invalidate(); await c.answer('تم الحذف');await web(c)
@router.callback_query(F.data=='addweb')
async def addweb(c,state):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    await state.set_state(A.web_name);await c.message.answer('اسم الموقع:');await c.answer()
@router.message(A.web_name)
async def wname(m,state):
    if not is_admin(m.from_user.id): return
    await state.update_data(name=m.text.strip());await state.set_state(A.web_search);await m.answer('رابط البحث، ويجب أن يحتوي {query} مكان الاستعلام.')
@router.message(A.web_search)
async def wsearch(m,state):
    if not is_admin(m.from_user.id): return
    url=m.text.strip();test=url.replace('{query}','test')
    if '{query}' not in url or not is_ssrf_safe_url(test):return await m.answer('❌ الرابط غير صالح/غير آمن.')
    d=await state.get_data();p=urlparse(test);base=f'{p.scheme}://{p.netloc}'
    await state.update_data(web_base=base, web_search_url=url)
    await state.set_state(A.web_purpose)
    await m.answer('📌 هذا الموقع مخصص لمن؟', reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📚 الجامعة — البحث',callback_data='web_purpose:university_search')],
        [InlineKeyboardButton(text='🎓 الجامعة — مشروع التخرج',callback_data='web_purpose:graduation')],
        [InlineKeyboardButton(text='🔎 الاثنان',callback_data='web_purpose:both')]
    ]))

# ---- Owner PDF publishing workflow -------------------------------------------------
# A PDF uploaded while the owner is adding a button is treated as content to
# publish, never as an AI document. The owner chooses the exact destination.

class PdfPlacementStates(StatesGroup):
    path = State()

async def _save_owner_pdf(bot, message, state, file_id, filename, mime_type='application/pdf'):
    d = await state.get_data()
    title = (d.get('label') or filename or 'ملف').strip()[:500]
    extracted = ''
    try:
        extracted = await extract_telegram_document(bot, file_id, filename, mime_type)
        extracted = (extracted or '').strip()
        if len(extracted) > MAX_STUDY_TEXT_CHARS:
            raise ValueError(
                f'النص المستخرج كبير جدًا ({len(extracted):,} حرف). الحد الآمن للمادة المحفوظة هو {MAX_STUDY_TEXT_CHARS:,} حرف؛ قسّم الكتاب إلى أجزاء أصغر.'
            )
        if not extracted:
            raise ValueError('تم رفع الملف لكن لم يتم استخراج أي نص منه.')
    except Exception as exc:
        reason = str(exc).strip() or repr(exc)
        log.exception('Owner PDF extraction failed file_id=%s filename=%s', file_id, filename)
        await log_error('owner.pdf.extract', reason, user_id=None)
        await message.answer(
            '❌ تعذر استخراج النص من الملف، لذلك لم يتم نشره بعد.\n\n'
            f'السبب: <code>{safe_html_escape(reason[:900])}</code>\n\n'
            'إذا كان PDF ممسوحًا كصور، تأكد من تفعيل Gemini/OCR ثم أعد رفعه.',
            parse_mode='HTML'
        )
        return False
    await state.update_data(
        pending_file_id=file_id, pending_filename=filename, pending_mime_type=mime_type,
        pending_title=title, pending_text_content=extracted
    )
    await state.set_state(PdfPlacementStates.path)
    await message.answer(
        '✅ <b>تم حفظ الملف مؤقتًا.</b>\n\n'
        '📍 <b>أين تريد أن تضع الزر تحديدًا؟</b>\n\n'
        'أرسل المسار بهذا الشكل:\n'
        '<code>مدرستي &gt; السادس المهني &gt; الصناعي &gt; الكهرباء &gt; المرحلة الثالثة &gt; الطبيعيات</code>\n\n'
        'ويمكنك استخدام أي مسار يناسبك. لن يتم إرسال الملف إلى الذكاء الاصطناعي.',
        parse_mode='HTML'
    )

async def process_admin_document(message, state):
    if not is_admin(message.from_user.id):
        return False
    doc = message.document
    if not doc:
        return False
    name = doc.file_name or 'file.pdf'
    if not name.lower().endswith('.pdf'):
        await message.answer('❌ إضافة الزر بالملف هنا تدعم PDF فقط.')
        return True
    await _save_owner_pdf(message.bot, message, state, doc.file_id, name, doc.mime_type or 'application/pdf')
    return True

async def _parse_placement(raw):
    """Parse a human-readable owner placement path.

    Supported example:
    مدرستي > السادس المهني > الصناعي > الكهرباء > المرحلة الثالثة > الطبيعيات > كتب
    The order is intentionally flexible; the owner can place the content exactly
    where it belongs instead of sending the file to AI.
    """
    parts = [x.strip() for x in raw.replace('→','>').split('>') if x.strip()]
    if not parts:
        return None
    top = parts[0].replace('🏫 ', '').strip()
    if top not in {'مدرستي', 'الجامعة', 'دراستي', 'دراسة حرة', 'القائمة الرئيسية'}:
        return None
    if top != 'مدرستي':
        # This workflow currently publishes school-library content.
        return None

    known_types = {
        'كتب': '📚 كتب', '📚 كتب': '📚 كتب',
        'ملازم': '📝 ملازم', '📝 ملازم': '📝 ملازم',
        'ملخصات': '📌 ملخصات', '📌 ملخصات': '📌 ملخصات',
        'مدرسين': '👨‍🏫 مدرسين', '👨‍🏫 مدرسين': '👨‍🏫 مدرسين',
        'وزاريات': '📋 وزاريات', '📋 وزاريات': '📋 وزاريات',
        'وزاريات السادس المهني': '📋 وزاريات السادس المهني', '📋 وزاريات السادس المهني': '📋 وزاريات السادس المهني',
        'قبولات': '🎓 قبولات', '🎓 قبولات': '🎓 قبولات',
        'شرح': '📘 شروحات', 'شروحات': '📘 شروحات', '📘 شروحات': '📘 شروحات',
    }
    content_type = next((known_types[x] for x in parts[1:] if x in known_types), None)
    if not content_type:
        content_type = '📚 كتب'
    without_type = [x for x in parts[1:] if x not in known_types]

    # Common placement tokens. We preserve unknown tokens as the deepest
    # meaningful level rather than rejecting a path created by the owner.
    placement = dict(scope='school', content_type=content_type, stage='', grade='', branch='', department='', subject='')
    for x in without_type:
        if x in {'المرحلة الأولى','المرحلة الثانية','المرحلة الثالثة'}:
            placement['stage'] = x
        elif x.startswith('السادس ') or x in {'السادس ابتدائي','الثالث متوسط'}:
            placement['grade'] = x
        elif x in {'علمي','أدبي'}:
            placement['branch'] = x
        elif x in {'الصناعي','التجاري','الحاسوب وتقنيات المعلومات','الفنون التطبيقية','السياحة والفندقة','العلوم الرياضية','الزراعي'}:
            placement['branch'] = 'مهني / ' + x
        elif placement['branch'].startswith('مهني /') and not placement['department']:
            placement['department'] = x
        elif not placement['subject']:
            placement['subject'] = x
        elif not placement['department']:
            placement['department'] = x
    return placement

@router.message(PdfPlacementStates.path, F.text)
async def owner_pdf_path(m, state):
    if not is_admin(m.from_user.id): return
    d = await state.get_data()
    placement = await _parse_placement(m.text.strip())
    if not placement:
        return await m.answer('❌ المسار غير مفهوم. مثال:\n<code>مدرستي &gt; السادس المهني &gt; الصناعي &gt; الكهرباء &gt; المرحلة الثالثة &gt; الطبيعيات</code>', parse_mode='HTML')
    from models import SchoolContent, SchoolPlacement
    async with async_session_maker() as s:
        # Legacy text-path publishing now uses the same normalized
        # one-file/many-placements model as the button workflow.
        item = SchoolContent(
            title=d.get('pending_title') or d.get('pending_filename') or 'ملف PDF',
            content_type=placement['content_type'],
            url='tgfile:'+str(d['pending_file_id']),
            file_id=d['pending_file_id'],
            mime_type=d.get('pending_mime_type') or 'application/pdf',
            text_content=(d.get('pending_text_content') or '').strip() or None,
            stage=placement['stage'], grade=placement['grade'],
            branch=placement['branch'], department=placement['department'],
            subject=placement['subject'], shared=True,
        )
        s.add(item)
        await s.flush()
        s.add(SchoolPlacement(content_id=item.id, **{k: placement[k] for k in ('stage','grade','branch','department','subject')}))
        await s.commit()
    await state.clear()
    await m.answer('✅ <b>تم حفظ الزر والملف بنجاح.</b>\n\n📍 المكان:\n'+safe_html_escape(m.text.strip())+'\n\n📄 الملف سيُرسل للمستخدم مباشرة، ولن يُرسل تلقائيًا إلى الذكاء الاصطناعي.', parse_mode='HTML', reply_markup=menu())


# ============================================================
# SCHOOL LIBRARY — ONE FILE, MANY PUBLICATION PLACEMENTS
# The PDF is stored once in school_contents. Each appearance in
# a school section is a lightweight row in school_placements.
# ============================================================
from sqlalchemy import or_
from models import SchoolPlacement

SCHOOL_KINDS = [
    ("📚 كتب","📚 كتب"),("📝 ملازم","📝 ملازم"),("📌 ملخصات","📌 ملخصات"),
    ("👨‍🏫 مدرسين","👨‍🏫 مدرسين"),("📘 شروحات","📘 شروحات"),("🎓 قبولات","🎓 قبولات"),
    ("📋 وزاريات السادس الابتدائي","📋 وزاريات"),
    ("📋 وزاريات الثالث المتوسط","📋 وزاريات"),
    ("📋 وزاريات السادس العلمي","📋 وزاريات"),
    ("📋 وزاريات السادس الأدبي","📋 وزاريات"),
    ("📋 وزاريات السادس المهني","📋 وزاريات السادس المهني"),
    ("📖 الخارجي","📖 الخارجي"),("📖 الدراسة الحرة","📖 الدراسة الحرة"),
]

VOC_BRANCHES = ["الصناعي","التجاري","الزراعي","الحاسوب وتقنيات المعلومات","الفنون التطبيقية","التمريض","السياحة والفندقة","العلوم الرياضية"]
INDUSTRIAL_DEPTS = [
    "الأجهزة الطبية","الكهرباء","الميكانيك","البناء","تكييف الهواء والتثليج",
    "تكرير النفط ومعالجة الغاز","الإلكترونيك والسيطرة","الاتصالات","صيانة المصاعد الكهربائية",
    "توليد الطاقة الكهربائية ونقلها","صيانة منظومات الليزر","تكنولوجيا الإعلام","السيارات",
    "اللحام وتشكيل المعادن","المكننة الزراعية","ميكاترونكس السيارات","تكنولوجيا السباكة",
    "تكنولوجيا صناعية","الصناعات الغذائية","الصناعات البتروكيمياوية","النجارة","الخزف والزجاج",
    "النسيج","الطباعة","معالجة المياه وشبكاتها","المساحة",
]
VOC_STAGES = ["المرحلة الأولى","المرحلة الثانية","المرحلة الثالثة"]

def school_owner_menu():
    rows=[[InlineKeyboardButton(text=a,callback_data="sc_add:"+b)] for a,b in SCHOOL_KINDS]
    rows += [
        [InlineKeyboardButton(text="📋 عرض المحتوى",callback_data="sc_list")],
        [InlineKeyboardButton(text="🗑 إدارة الحذف",callback_data="sc_delete_list")],
        [InlineKeyboardButton(text="✏️ تعديل أماكن النشر",callback_data="sc_edit_list")],
        [InlineKeyboardButton(text="🏠 لوحة المالك",callback_data="admin_home")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.callback_query(F.data=="adm_school")
async def adm_school(c, state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    await state.clear()
    await c.message.answer("🏫 <b>إدارة محتوى مدرستي</b>\n\nاختر نوع المحتوى ثم ارفع الملف مرة واحدة.",parse_mode="HTML",reply_markup=school_owner_menu())
    await c.answer()

@router.callback_query(F.data.startswith("sc_add:"))
async def sc_add(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    kind=c.data.split(":",1)[1]
    await state.update_data(school_kind=kind)
    await state.set_state(A.school_file)
    await c.message.answer(
        f"➕ <b>{kind}</b>\n\n"
        "أرسل المحتوى مرة واحدة، ويمكن أن يكون:\n"
        "📝 نص\n🔗 رابط http/https\n🖼 صورة\n🎬 فيديو\n📎 ملف (PDF / DOCX / PPTX / TXT)\n\n"
        "بعد استلامه ستختار أكثر من قسم/مادة للنشر بدون إعادة الإرسال.",
        parse_mode="HTML"
    )
    await c.answer()

async def _begin_school_content(message, state, *, content_kind, text_content="", url="", photo_id="", video_id="", file_id="", filename="", mime_type=""):
    """Store one owner content item in FSM; publication paths are selected later."""
    text_content=(text_content or "").strip()
    if len(text_content) > MAX_STUDY_TEXT_CHARS:
        await message.answer(
            f"❌ النص المستخرج كبير جدًا ({len(text_content):,} حرف). "
            f"الحد الآمن للمادة المحفوظة هو {MAX_STUDY_TEXT_CHARS:,} حرف؛ قسّم الكتاب إلى أجزاء أصغر."
        )
        return
    url=(url or "").strip()[:2000]
    await state.update_data(
        pending_text_content=text_content,
        pending_url=url,
        pending_photo_id=photo_id or None,
        pending_video_id=video_id or None,
        pending_file_id=file_id or None,
        pending_filename=filename or "",
        pending_mime_type=mime_type or "",
        default_title=(filename.rsplit(".",1)[0] if filename else (text_content[:80] or content_kind))[:500],
    )
    await state.set_state(A.school_title)
    d=await state.get_data()
    await message.answer(
        f"✅ تم استلام {content_kind}.\n\n"
        f"✏️ أرسل عنوان المحتوى، أو اكتب <code>نفس</code> لاستخدام العنوان المقترح:\n"
        f"<b>{safe_html_escape(d.get('default_title','محتوى'))}</b>",
        parse_mode="HTML"
    )

@router.message(A.school_file, F.document)
async def owner_school_document_input(m, state):
    if not is_admin(m.from_user.id): return
    doc=m.document
    from config import MAX_FILE_SIZE, SUPPORTED_EXTENSIONS
    name=doc.file_name or "ملف"
    ext=Path(name).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return await m.answer("❌ صيغة الملف غير مدعومة. المسموح: PDF / DOCX / PPTX / TXT.")
    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        return await m.answer(f"❌ الملف كبير جدًا. الحد {MAX_FILE_SIZE/1024/1024:.0f} MB.")
    d=await state.get_data()
    await m.answer('🔎 جارٍ استخراج النص وفهرسة الملف ليظهر في البحث...')
    extracted=''
    try:
        extracted=await extract_telegram_document(m.bot,doc.file_id,name,doc.mime_type)
    except Exception as exc:
        reason = str(exc).strip() or repr(exc)
        log.exception('School document extraction failed file_id=%s filename=%s', doc.file_id, name)
        await log_error('owner.school.extract', reason, user_id=None)
        return await m.answer(f'❌ تعذر استخراج النص من الملف.\nالسبب: <code>{safe_html_escape(reason[:900])}</code>', parse_mode='HTML')
    if not (extracted or '').strip():
        reason='تم تنزيل الملف لكن لم يتم استخراج نص صالح منه.'
        await log_error('owner.school.extract', reason, user_id=None)
        return await m.answer('❌ ' + reason)
    await _begin_school_content(m,state,content_kind=d.get("school_kind","المحتوى"),text_content=extracted,file_id=doc.file_id,filename=name,mime_type=doc.mime_type or "application/octet-stream")

@router.message(A.school_file, F.photo)
async def owner_school_photo_input(m, state):
    if not is_admin(m.from_user.id): return
    d=await state.get_data(); caption=(m.caption or "").strip()
    await _begin_school_content(m,state,content_kind=d.get("school_kind","المحتوى"),photo_id=m.photo[-1].file_id,text_content=caption)

@router.message(A.school_file, F.video)
async def owner_school_video_input(m, state):
    if not is_admin(m.from_user.id): return
    d=await state.get_data(); caption=(m.caption or "").strip()
    await _begin_school_content(m,state,content_kind=d.get("school_kind","المحتوى"),video_id=m.video.file_id,text_content=caption)

@router.message(A.school_file, F.text)
async def owner_school_text_or_link_input(m, state):
    if not is_admin(m.from_user.id): return
    value=m.text.strip()
    if not value: return await m.answer("❌ المحتوى لا يمكن أن يكون فارغًا.")
    d=await state.get_data()
    if re.match(r"^https?://\S+$", value, re.I):
        await _begin_school_content(m,state,content_kind=d.get("school_kind","المحتوى"),url=value)
    else:
        await _begin_school_content(m,state,content_kind=d.get("school_kind","المحتوى"),text_content=value)

async def process_owner_school_document(message,state):
    if not is_admin(message.from_user.id) or not message.document: return False
    if await state.get_state() != A.school_file.state: return False
    doc=message.document
    filename=doc.file_name or "ملف"
    ext=Path(filename).suffix.lower()
    from config import MAX_FILE_SIZE, SUPPORTED_EXTENSIONS
    if ext not in SUPPORTED_EXTENSIONS:
        await message.answer("❌ صيغة الملف غير مدعومة. المسموح: PDF / DOCX / PPTX / TXT.")
        return True
    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await message.answer(f"❌ الملف كبير جدًا ({doc.file_size/1024/1024:.1f} MB). الحد: {MAX_FILE_SIZE/1024/1024:.0f} MB.")
        return True
    extracted=''
    try:
        await message.answer('🔎 جارٍ استخراج النص وفهرسة الملف...')
        extracted=await extract_telegram_document(message.bot,doc.file_id,filename,doc.mime_type)
    except Exception as exc:
        reason = str(exc).strip() or repr(exc)
        log.exception('Owner school document extraction failed file_id=%s filename=%s', doc.file_id, filename)
        await log_error('owner.school.extract', reason, user_id=None)
        return await message.answer(f'❌ تعذر استخراج النص من الملف.\nالسبب: <code>{safe_html_escape(reason[:900])}</code>', parse_mode='HTML')
    if not (extracted or '').strip():
        reason='تم تنزيل الملف لكن لم يتم استخراج نص صالح منه.'
        await log_error('owner.school.extract', reason, user_id=None)
        return await message.answer('❌ ' + reason)
    if len(extracted) > MAX_STUDY_TEXT_CHARS:
        reason=(
            f'النص المستخرج كبير جدًا ({len(extracted):,} حرف). '
            f'الحد الآمن للمادة المحفوظة هو {MAX_STUDY_TEXT_CHARS:,} حرف؛ قسّم الكتاب إلى أجزاء أصغر.'
        )
        await log_error('owner.school.extract', reason, user_id=None)
        return await message.answer('❌ ' + reason)
    await state.update_data(
        pending_file_id=doc.file_id,
        pending_filename=filename,
        pending_mime_type=doc.mime_type or "application/octet-stream",
        pending_text_content=extracted,
    )
    await state.set_state(A.school_title)
    default=(doc.file_name or "ملف").rsplit(".",1)[0][:500]
    await state.update_data(default_title=default)
    await message.answer(
        f"📄 <b>تم استلام الملف:</b> {safe_html_escape(default)}\n\n"
        "✏️ أرسل اسم الكتاب/الملزمة، أو اكتب <code>نفس</code> لاستخدام اسم الملف.",
        parse_mode="HTML"
    )
    return True

def parse_school_path(raw,kind):
    """Legacy text-path parser retained for compatibility."""
    parts=[x.strip() for x in raw.replace("→",">").split(">") if x.strip()]
    p={"stage":"","grade":"","branch":"","department":"","subject":""}
    grades={"الأول ابتدائي","الثاني ابتدائي","الثالث ابتدائي","الرابع ابتدائي","الخامس ابتدائي","السادس ابتدائي",
            "الأول متوسط","الثاني متوسط","الثالث متوسط","الرابع العلمي","الخامس العلمي","السادس العلمي",
            "الرابع الأدبي","الخامس الأدبي","السادس الأدبي","السادس المهني"}
    voc=set(VOC_BRANCHES)
    for x in parts:
        if x in VOC_STAGES: p["stage"]=x
        elif x in grades: p["grade"]=x
        elif x in {"علمي","أدبي"}: p["branch"]=x
        elif x in voc: p["branch"]="مهني / "+x
        elif x.startswith("مهني /"): p["branch"]=x
        elif p["branch"].startswith("مهني /") and not p["department"] and x!="عام": p["department"]=x
        elif not p["subject"]: p["subject"]=x
        elif not p["department"]: p["department"]=x
    if kind=="📋 وزاريات" and not p["grade"]: return None
    if kind=="📋 وزاريات السادس المهني" and not p["grade"]: p["grade"]="السادس المهني"
    return p

@router.message(A.school_title,F.text)
async def owner_school_title(m,state):
    if not is_admin(m.from_user.id): return
    d=await state.get_data()
    title=m.text.strip()
    if title=="نفس": title=d.get("default_title") or d.get("pending_filename") or "ملف"
    title=title[:500]
    await state.update_data(pending_title=title, selected_placements=[])
    await state.set_state(A.school_publish_branch)
    await m.answer(
        "📍 <b>اختر أماكن نشر الملف</b>\n\n"
        "يمكنك إضافة الملف إلى <b>أكثر من مرحلة وفرع وقسم</b> بدون إعادة رفعه.\n"
        "كل ملف يُحفظ مرة واحدة، وأماكن ظهوره تُحفظ كروابط مستقلة.",
        parse_mode="HTML", reply_markup=_publish_root_kb()
    )

def _publish_root_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏫 الابتدائية",callback_data="sc_pub_scope:primary")],
        [InlineKeyboardButton(text="🏫 المتوسطة",callback_data="sc_pub_scope:middle")],
        [InlineKeyboardButton(text="🔬 السادس العلمي",callback_data="sc_pub_scope:scientific")],
        [InlineKeyboardButton(text="📚 السادس الأدبي",callback_data="sc_pub_scope:literary")],
        [InlineKeyboardButton(text="🛠 المهني",callback_data="sc_pub_scope:vocational")],
        [InlineKeyboardButton(text="📖 الخارجي",callback_data="sc_pub_scope:external")],
        [InlineKeyboardButton(text="📋 عرض المحدد حاليًا",callback_data="sc_pub_summary")],
        [InlineKeyboardButton(text="🏠 إلغاء",callback_data="adm_school")],
    ])

@router.callback_query(F.data.startswith("sc_pub_scope:"))
async def sc_pub_scope(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    scope=c.data.split(":",1)[1]
    await state.update_data(pub_scope=scope)
    if scope=="primary":
        from school_catalog import PRIMARY
        grades=list(PRIMARY)
        rows=[[InlineKeyboardButton(text=x,callback_data=f"sc_pub_grade:{i}")] for i,x in enumerate(grades)]
        rows.append([InlineKeyboardButton(text="⬅️ الأقسام الرئيسية",callback_data="sc_pub_root")])
        await state.update_data(pub_grades=grades)
        await c.message.answer("🏫 اختر الصف الابتدائي:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif scope=="middle":
        from school_catalog import INTERMEDIATE
        grades=list(INTERMEDIATE)
        rows=[[InlineKeyboardButton(text=x,callback_data=f"sc_pub_grade:{i}")] for i,x in enumerate(grades)]
        rows.append([InlineKeyboardButton(text="⬅️ الأقسام الرئيسية",callback_data="sc_pub_root")])
        await state.update_data(pub_grades=grades)
        await c.message.answer("🏫 اختر الصف المتوسط:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif scope in {"scientific","literary"}:
        from school_catalog import PREPARATORY
        branch="علمي" if scope=="scientific" else "أدبي"
        grades=list(PREPARATORY[branch])
        rows=[[InlineKeyboardButton(text=x,callback_data=f"sc_pub_grade:{i}")] for i,x in enumerate(grades)]
        rows.append([InlineKeyboardButton(text="⬅️ الأقسام الرئيسية",callback_data="sc_pub_root")])
        await state.update_data(pub_branch=branch,pub_grades=grades)
        await c.message.answer(f"📚 اختر صف {branch}:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif scope=="external":
        # الطالب الخارجي له مسار مستقل: ابتدائي -> سادس ابتدائي،
        # متوسط -> ثالث متوسط، إعدادي -> علمي/أدبي ثم الصف،
        # مهني -> الفرع -> القسم -> المرحلة.
        rows=[
            [InlineKeyboardButton(text="🏫 ابتدائي",callback_data="sc_ext_level:primary")],
            [InlineKeyboardButton(text="🏫 متوسطة",callback_data="sc_ext_level:middle")],
            [InlineKeyboardButton(text="📚 إعدادية",callback_data="sc_ext_level:prep")],
            [InlineKeyboardButton(text="🛠 مهني",callback_data="sc_ext_level:vocational")],
            [InlineKeyboardButton(text="⬅️ الأقسام الرئيسية",callback_data="sc_pub_root")],
        ]
        await state.update_data(pub_branch="خارجي",pub_external=True)
        await c.message.answer("📖 <b>طالب خارجي</b>\n\nاختر نوع الدراسة:",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    else:
        from school_catalog import VOCATIONAL_BRANCHES
        rows=[[InlineKeyboardButton(text=x,callback_data=f"sc_pub_vbranch:{i}")] for i,x in enumerate(VOCATIONAL_BRANCHES)]
        rows.append([InlineKeyboardButton(text="⬅️ الأقسام الرئيسية",callback_data="sc_pub_root")])
        await state.update_data(pub_vbranches=VOCATIONAL_BRANCHES)
        await c.message.answer("🛠 اختر الفرع المهني:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()

@router.callback_query(F.data=="sc_pub_root")
async def sc_pub_root(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    await c.message.answer("📍 اختر مكان نشر آخر للملف:",reply_markup=_publish_root_kb()); await c.answer()

@router.callback_query(F.data.startswith("sc_pub_branch:"))
async def sc_pub_branch(c,state):
    """Open the vocational branch/stage picker for the publish workflow.

    This callback is also used by the summary's Back button and by the
    edit-existing-file flow, so it must be a real handler rather than a
    callback that points to a non-existent route.
    """
    if not is_admin(c.from_user.id):
        return await c.answer("غير مسموح", show_alert=True)
    branch = c.data.split(":", 1)[1].strip()
    from school_catalog import VOCATIONAL_BRANCHES
    if branch not in VOCATIONAL_BRANCHES:
        return await c.answer("خيار غير صالح", show_alert=True)
    await state.update_data(pub_scope="vocational", pub_external=False, pub_branch=branch)
    rows=[[InlineKeyboardButton(text=x, callback_data=f"sc_pub_stage:{x}")] for x in VOC_STAGES]
    rows.append([InlineKeyboardButton(text="⬅️ الفروع المهنية", callback_data="sc_pub_scope:vocational")])
    await c.message.answer(
        f"🛠 {branch}\n\nاختر المرحلة المهنية:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await c.answer()

@router.callback_query(F.data.startswith("sc_pub_vbranch:"))
async def sc_pub_vbranch(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    d=await state.get_data(); vals=d.get("pub_vbranches",[])
    try: branch=vals[int(c.data.split(":")[1])]
    except Exception: return await c.answer("خيار غير صالح",show_alert=True)
    await state.update_data(pub_branch=branch)
    rows=[[InlineKeyboardButton(text=x,callback_data=f"sc_pub_stage:{x}")] for x in VOC_STAGES]
    rows.append([InlineKeyboardButton(text="⬅️ الفروع المهنية",callback_data="sc_pub_scope:vocational")])
    await c.message.answer("📚 اختر المرحلة المهنية:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)); await c.answer()

@router.callback_query(F.data.startswith("sc_pub_stage:"))
async def sc_pub_stage(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    stage=c.data.split(":",1)[1]; d=await state.get_data(); branch=d.get("pub_branch","")
    await state.update_data(pub_stage=stage)
    from school_catalog import subjects_for, VOCATIONAL_DEPARTMENTS, PRIMARY, INTERMEDIATE, PREPARATORY
    scope=d.get("pub_scope")
    if scope=="vocal" or scope=="vocational":
        depts=list(VOCATIONAL_DEPARTMENTS.get(branch,[]))
        subject_set=set()
        for dept in depts:
            subject_set.update(subjects_for("", "", branch, branch, dept, stage) or [])
        subjects=sorted(subject_set) or ["عام"]
    else:
        subjects=[]
        grade=d.get("pub_grade","")
        if scope=="primary": subjects=PRIMARY.get(grade,[])
        elif scope=="middle": subjects=INTERMEDIATE.get(grade,[])
        elif scope in {"scientific","literary"}: subjects=PREPARATORY.get(branch,{}).get(grade,[])
        elif scope=="external":
            if grade=="السادس الابتدائي": subjects=PRIMARY.get(grade,[])
            elif grade=="الثالث المتوسط": subjects=INTERMEDIATE.get(grade,[])
            elif grade=="السادس العلمي": subjects=PREPARATORY.get("علمي",{}).get(grade,[])
            elif grade=="السادس الأدبي": subjects=PREPARATORY.get("أدبي",{}).get(grade,[])
            else: subjects=["عام"]
        subjects=subjects or ["عام"]
    await state.update_data(pub_subjects=subjects)
    rows=[[InlineKeyboardButton(text=x,callback_data=f"sc_pub_subject:{i}")] for i,x in enumerate(subjects)]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع",callback_data="sc_pub_root")])
    await c.message.answer("📖 اختر المادة:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)); await c.answer()

@router.callback_query(F.data.startswith("sc_ext_level:"))
async def sc_ext_level(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    level=c.data.split(":",1)[1]
    await state.update_data(pub_scope="external",pub_external=True,pub_branch="خارجي")
    if level=="primary":
        await state.update_data(pub_grade="السادس الابتدائي",pub_branch="خارجي",pub_stage="")
        from school_catalog import PRIMARY
        subjects=PRIMARY.get("السادس الابتدائي",[]) or ["عام"]
        await state.update_data(pub_subjects=subjects)
        rows=[[InlineKeyboardButton(text=x,callback_data=f"sc_pub_subject:{i}")] for i,x in enumerate(subjects)]
        rows.append([InlineKeyboardButton(text="⬅️ رجوع",callback_data="sc_pub_scope:external")])
        await c.message.answer("🏫 الخارجي → اختر مادة السادس الابتدائي:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif level=="middle":
        await state.update_data(pub_grade="الثالث المتوسط",pub_branch="خارجي",pub_stage="")
        from school_catalog import INTERMEDIATE
        subjects=INTERMEDIATE.get("الثالث المتوسط",[]) or ["عام"]
        await state.update_data(pub_subjects=subjects)
        rows=[[InlineKeyboardButton(text=x,callback_data=f"sc_pub_subject:{i}")] for i,x in enumerate(subjects)]
        rows.append([InlineKeyboardButton(text="⬅️ رجوع",callback_data="sc_pub_scope:external")])
        await c.message.answer("🏫 الخارجي → اختر مادة الثالث المتوسط:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif level=="prep":
        rows=[[InlineKeyboardButton(text="🔬 علمي",callback_data="sc_ext_pbranch:علمي")],
              [InlineKeyboardButton(text="📚 أدبي",callback_data="sc_ext_pbranch:أدبي")],
              [InlineKeyboardButton(text="⬅️ رجوع",callback_data="sc_pub_scope:external")]]
        await c.message.answer("📚 الخارجي → اختر الفرع الإعدادي:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    else:
        from school_catalog import VOCATIONAL_BRANCHES
        await state.update_data(pub_vbranches=VOCATIONAL_BRANCHES)
        rows=[[InlineKeyboardButton(text=x,callback_data=f"sc_ext_vbranch:{i}")] for i,x in enumerate(VOCATIONAL_BRANCHES)]
        rows.append([InlineKeyboardButton(text="⬅️ رجوع",callback_data="sc_pub_scope:external")])
        await c.message.answer("🛠 الخارجي → اختر الفرع المهني:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()

@router.callback_query(F.data.startswith("sc_ext_pbranch:"))
async def sc_ext_pbranch(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    branch=c.data.split(":",1)[1]
    grades=["الرابع العلمي","الخامس العلمي","السادس العلمي"] if branch=="علمي" else ["الرابع الأدبي","الخامس الأدبي","السادس الأدبي"]
    await state.update_data(pub_branch="خارجي / "+branch,pub_ext_branch=branch,pub_grades=grades)
    rows=[[InlineKeyboardButton(text=x,callback_data=f"sc_ext_pgrade:{i}")] for i,x in enumerate(grades)]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع",callback_data="sc_ext_level:prep")])
    await c.message.answer(f"📚 الخارجي → {branch} → اختر الصف:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)); await c.answer()

@router.callback_query(F.data.startswith("sc_ext_pgrade:"))
async def sc_ext_pgrade(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    d=await state.get_data(); grades=d.get("pub_grades",[])
    try: grade=grades[int(c.data.split(":")[1])]
    except Exception: return await c.answer("خيار غير صالح",show_alert=True)
    branch=d.get("pub_ext_branch","")
    from school_catalog import PREPARATORY
    subjects=PREPARATORY.get(branch,{}).get(grade,[]) or ["عام"]
    await state.update_data(pub_grade=grade,pub_subjects=subjects)
    rows=[[InlineKeyboardButton(text=x,callback_data=f"sc_pub_subject:{i}")] for i,x in enumerate(subjects)]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع",callback_data=f"sc_ext_pbranch:{branch}")])
    await c.message.answer(f"📚 الخارجي → {branch} → {grade} → اختر المادة:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)); await c.answer()

@router.callback_query(F.data.startswith("sc_ext_vbranch:"))
async def sc_ext_vbranch(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    from school_catalog import VOCATIONAL_BRANCHES, VOCATIONAL_DEPARTMENTS
    vals=VOCATIONAL_BRANCHES
    try: branch=vals[int(c.data.split(":")[1])]
    except Exception: return await c.answer("خيار غير صالح",show_alert=True)
    depts=list(VOCATIONAL_DEPARTMENTS.get(branch,[])) or ["عام"]
    await state.update_data(pub_branch="خارجي / مهني / "+branch,pub_ext_vbranch=branch,pub_ext_depts=depts)
    rows=[[InlineKeyboardButton(text=x,callback_data=f"sc_ext_vdept:{i}")] for i,x in enumerate(depts)]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع",callback_data="sc_ext_level:vocational")])
    await c.message.answer(f"🛠 الخارجي → مهني → {branch} → اختر القسم:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)); await c.answer()

@router.callback_query(F.data.startswith("sc_ext_vdept:"))
async def sc_ext_vdept(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    d=await state.get_data(); depts=d.get("pub_ext_depts",[])
    from school_catalog import VOCATIONAL_BRANCHES
    try: dept=depts[int(c.data.split(":")[1])]
    except Exception: return await c.answer("خيار غير صالح",show_alert=True)
    branch=d.get("pub_ext_vbranch","")
    await state.update_data(pub_branch="خارجي / مهني / "+branch,pub_department=dept,pub_grade="السادس المهني",pub_stage="")
    # For vocational external material, show the three vocational stages.
    stages=VOC_STAGES
    rows=[[InlineKeyboardButton(text=x,callback_data=f"sc_ext_vstage:{i}")] for i,x in enumerate(stages)]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع",callback_data=f"sc_ext_vbranch:{VOC_BRANCHES.index(branch) if branch in VOC_BRANCHES else 0}")])
    await c.message.answer(f"🛠 الخارجي → مهني → {branch} → {dept} → اختر المرحلة:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)); await c.answer()

@router.callback_query(F.data.startswith("sc_ext_vstage:"))
async def sc_ext_vstage(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    d=await state.get_data()
    try: stage=VOC_STAGES[int(c.data.split(":")[1])]
    except Exception: return await c.answer("خيار غير صالح",show_alert=True)
    branch=d.get("pub_ext_vbranch",""); dept=d.get("pub_department","")
    from school_catalog import subjects_for
    subjects=subjects_for("", "", branch, branch, dept, stage) or ["عام"]
    await state.update_data(pub_stage=stage,pub_subjects=subjects)
    rows=[[InlineKeyboardButton(text=x,callback_data=f"sc_pub_subject:{i}")] for i,x in enumerate(subjects)]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع",callback_data=f"sc_ext_vdept:{d.get('pub_ext_depts',[]).index(dept) if dept in d.get('pub_ext_depts',[]) else 0}")])
    await c.message.answer(f"🛠 الخارجي → مهني → {branch} → {dept} → {stage} → اختر المادة:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)); await c.answer()

@router.callback_query(F.data.startswith("sc_pub_grade:"))
async def sc_pub_grade(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    d=await state.get_data(); vals=d.get("pub_grades",[])
    try: grade=vals[int(c.data.split(":")[1])]
    except Exception: return await c.answer("خيار غير صالح",show_alert=True)
    await state.update_data(pub_grade=grade)
    scope=d.get("pub_scope")
    if scope=="primary": branch="ابتدائية"
    elif scope=="middle": branch="متوسطة"
    elif scope=="external": branch="خارجي"
    else: branch=d.get("pub_branch","")
    await state.update_data(pub_branch=branch)
    if scope in {"primary","middle","scientific","literary","external"}:
        await sc_pub_stage(c,state) if False else None
        from school_catalog import PRIMARY,INTERMEDIATE,PREPARATORY
        if scope=="primary": subjects=PRIMARY.get(grade,[])
        elif scope=="middle": subjects=INTERMEDIATE.get(grade,[])
        elif scope=="scientific": subjects=PREPARATORY.get("علمي",{}).get(grade,[])
        elif scope=="literary": subjects=PREPARATORY.get("أدبي",{}).get(grade,[])
        else:
            subjects=(PRIMARY.get(grade,[]) or INTERMEDIATE.get(grade,[]) or PREPARATORY.get("علمي",{}).get(grade,[]) or PREPARATORY.get("أدبي",{}).get(grade,[]) or ["عام"])
        await state.update_data(pub_subjects=subjects)
        rows=[[InlineKeyboardButton(text=x,callback_data=f"sc_pub_subject:{i}")] for i,x in enumerate(subjects)]
        rows.append([InlineKeyboardButton(text="⬅️ الأقسام الرئيسية",callback_data="sc_pub_root")])
        await c.message.answer("📖 اختر المادة:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()

@router.callback_query(F.data.startswith("sc_pub_subject:"))
async def sc_pub_subject(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    d=await state.get_data()
    try: subject=d["pub_subjects"][int(c.data.split(":")[1])]
    except Exception: return await c.answer("خيار غير صالح",show_alert=True)
    await state.update_data(pub_subject=subject)
    scope=d.get("pub_scope"); branch=d.get("pub_branch",""); grade=d.get("pub_grade",""); stage=d.get("pub_stage","")
    if scope in {"primary","middle","scientific","literary","external"}:
        if scope=="primary": path_branch="ابتدائية"; placement_stage="🏫 ابتدائية"
        elif scope=="middle": path_branch="متوسطة"; placement_stage="🏫 متوسطة"
        elif scope=="external":
            path_branch=branch or "خارجي"; placement_stage=""
            # Keep the external hierarchy in branch/department/stage.
        else: path_branch=branch; placement_stage="🏫 إعدادية"
        ext_department = d.get("pub_department", "") if scope=="external" else ""
        ext_stage = stage if scope=="external" and stage else placement_stage
        await _add_placement_to_state(state,{"stage":ext_stage,"grade":grade,"branch":path_branch,"department":ext_department,"subject":subject})
        await _show_publish_summary(c,state)
        return await c.answer()
    depts=INDUSTRIAL_DEPTS if branch=="الصناعي" else []
    from school_catalog import VOCATIONAL_DEPARTMENTS
    if not depts: depts=list(VOCATIONAL_DEPARTMENTS.get(branch,[]))
    if not depts:
        await _add_placement_to_state(state,{"stage":stage,"grade":"السادس المهني","branch":"مهني / "+branch,"department":"","subject":subject})
        await _show_publish_summary(c,state); return await c.answer()
    await _show_department_picker(c,state,depts); await c.answer()

async def _current_placements(state):
    d=await state.get_data()
    return list(d.get("selected_placements") or [])

def _placement_key(p):
    return "|".join(p.get(k,"") for k in ("grade","branch","department","stage","subject"))

async def _add_placement_to_state(state,p):
    rows=await _current_placements(state)
    if _placement_key(p) not in {_placement_key(x) for x in rows}: rows.append(p)
    await state.update_data(selected_placements=rows)

async def _show_department_picker(c,state,depts):
    d=await state.get_data(); selected=await _current_placements(state)
    keys={_placement_key(x) for x in selected}
    stage=d.get("pub_stage",""); branch=d.get("pub_branch",""); subject=d.get("pub_subject","")
    rows=[]
    for dept in depts:
        p={"stage":stage,"grade":"السادس المهني","branch":"مهني / "+branch,"department":dept,"subject":subject}
        mark="☑️" if _placement_key(p) in keys else "☐"
        rows.append([InlineKeyboardButton(text=f"{mark} {dept}",callback_data=f"sc_pub_dept:{depts.index(dept)}")])
    if branch == "الصناعي":
        rows.append([InlineKeyboardButton(text="☑️ كل أقسام الفرع الصناعي",callback_data="sc_pub_all_industrial")])
    rows.append([InlineKeyboardButton(text="⬅️ تغيير المادة",callback_data=f"sc_pub_stage:{stage}")])
    rows.append([InlineKeyboardButton(text="✅ حفظ في الأقسام المحددة",callback_data="sc_pub_save")])
    await c.message.answer(
        f"📄 <b>{safe_html_escape(d.get('pending_title','ملف'))}</b>\n"
        f"📍 {safe_html_escape(stage)} › {safe_html_escape(branch)} › {safe_html_escape(subject)}\n\n"
        "حدد قسمًا أو عدة أقسام:",
        parse_mode="HTML",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )

@router.callback_query(F.data.startswith("sc_pub_dept:"))
async def sc_pub_dept(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    d=await state.get_data(); from school_catalog import VOCATIONAL_DEPARTMENTS
    depts=INDUSTRIAL_DEPTS if d.get("pub_branch")=="الصناعي" else list(VOCATIONAL_DEPARTMENTS.get(d.get("pub_branch",""), []))
    try: dept=depts[int(c.data.split(":")[1])]
    except Exception: return await c.answer("خيار غير صالح",show_alert=True)
    p={"stage":d.get("pub_stage",""),"grade":"السادس المهني","branch":"مهني / "+d.get("pub_branch",""),"department":dept,"subject":d.get("pub_subject","")}
    rows=await _current_placements(state)
    key=_placement_key(p)
    if key in {_placement_key(x) for x in rows}: rows=[x for x in rows if _placement_key(x)!=key]
    else: rows.append(p)
    await state.update_data(selected_placements=rows)
    await _show_department_picker(c,state,depts)
    await c.answer()

@router.callback_query(F.data=="sc_pub_all_industrial")
async def sc_pub_all_industrial(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    d=await state.get_data()
    if d.get("pub_branch") != "الصناعي":
        return await c.answer("هذا الخيار متاح للصناعي فقط", show_alert=True)
    rows=await _current_placements(state)
    for dept in INDUSTRIAL_DEPTS:
        p={"stage":d.get("pub_stage",""),"grade":"السادس المهني","branch":"مهني / الصناعي","department":dept,"subject":d.get("pub_subject","")}
        if _placement_key(p) not in {_placement_key(x) for x in rows}: rows.append(p)
    await state.update_data(selected_placements=rows)
    await _show_department_picker(c,state,INDUSTRIAL_DEPTS)
    await c.answer("☑️ تم تحديد كل أقسام الصناعي")

@router.callback_query(F.data=="sc_pub_save")
async def sc_pub_save(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    d=await state.get_data(); placements=await _current_placements(state)
    if not placements: return await c.answer("حدد قسمًا واحدًا على الأقل",show_alert=True)
    if not _validate_school_placements(d.get("school_kind", ""), placements):
        return await c.answer("هذا النوع من المحتوى لا يطابق الصف/الفرع المحدد. اختر المكان الصحيح.", show_alert=True)
    if d.get("edit_content_id"):
        cid=int(d["edit_content_id"])
        async with async_session_maker() as s:
            existing=(await s.execute(select(SchoolPlacement).where(SchoolPlacement.content_id==cid))).scalars().all()
            keys={_placement_key({"grade":x.grade,"branch":x.branch,"department":x.department,"stage":x.stage,"subject":x.subject}) for x in existing}
            for p in placements:
                if _placement_key(p) not in keys:
                    s.add(SchoolPlacement(content_id=cid,**p))
            await s.commit()
        search_engine.invalidate()
        await state.clear()
        await c.message.answer("✅ تمت إضافة أماكن النشر بدون إعادة رفع الملف.",reply_markup=school_owner_menu())
        return await c.answer()
    async with async_session_maker() as s:
        # One physical Telegram file row; all destinations are separate placements.
        # Also mirror the first placement into legacy columns for compatibility
        # with older database/reporting code.
        first = placements[0]
        item=SchoolContent(
            title=d.get("pending_title") or d.get("pending_filename") or "ملف",
            content_type=d.get("school_kind","📚 كتب"),
            url=d.get("pending_url") or ("tgfile:"+str(d["pending_file_id"]) if d.get("pending_file_id") else None),
            text_content=d.get("pending_text_content") or None,
            photo_id=d.get("pending_photo_id"),
            video_id=d.get("pending_video_id"),
            file_id=d.get("pending_file_id"),
            mime_type=d.get("pending_mime_type") or None, shared=True,
            stage=first.get("stage",""), grade=first.get("grade",""),
            branch=first.get("branch",""), department=first.get("department",""),
            subject=first.get("subject","")
        )
        s.add(item); await s.flush()
        for p in placements:
            s.add(SchoolPlacement(content_id=item.id,**p))
        await s.commit()
    search_engine.invalidate()
    await record_event(c.from_user.id,'file_added',metadata={'content_type':d.get('school_kind','')})
    if d.get('school_kind') == '📚 كتب':
        await record_event(c.from_user.id,'book_added',metadata={'content_id':item.id})
    await state.clear()
    await c.message.answer(
        f"✅ <b>تم الحفظ بنجاح</b>\n\n📄 {safe_html_escape(item.title)}\n"
        f"📍 تم ربط الملف بـ <b>{len(placements)}</b> قسمًا.\n\n"
        "💾 الملف نفسه محفوظ مرة واحدة فقط في قاعدة البيانات.",
        parse_mode="HTML",reply_markup=school_owner_menu()
    )
    await c.answer()

@router.callback_query(F.data=="sc_pub_summary")
async def sc_pub_summary(c,state):
    if not is_admin(c.from_user.id):
        return await c.answer("غير مسموح", show_alert=True)
    placements=await _current_placements(state)
    if not placements:
        return await c.answer("لا توجد أماكن محددة حاليًا", show_alert=True)
    await _show_publish_summary(c,state)
    await c.answer()

@router.callback_query(F.data=="sc_pub_back")
async def sc_pub_back(c,state):
    if not is_admin(c.from_user.id):
        return await c.answer("غير مسموح", show_alert=True)
    d=await state.get_data()
    scope=d.get("pub_scope")
    if scope in {"vocational"}:
        branch=d.get("pub_branch", "")
        if branch in VOC_BRANCHES:
            rows=[[InlineKeyboardButton(text=x, callback_data=f"sc_pub_stage:{x}")] for x in VOC_STAGES]
            rows.append([InlineKeyboardButton(text="⬅️ الفروع المهنية", callback_data="sc_pub_scope:vocational")])
            await c.message.answer(f"🛠 {branch}\n\nاختر المرحلة المهنية:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        else:
            await c.message.answer("📍 اختر مكان نشر آخر للملف:", reply_markup=_publish_root_kb())
    else:
        await c.message.answer("📍 اختر مكان نشر آخر للملف:", reply_markup=_publish_root_kb())
    await c.answer()

def _validate_school_placements(kind, placements):
    """Prevent publishing a grade-specific school category into the wrong path."""
    rules = {
        "📋 وزاريات السادس الابتدائي": lambda p: p.get("grade") == "السادس الابتدائي" and p.get("branch") == "ابتدائية",
        "📋 وزاريات الثالث المتوسط": lambda p: p.get("grade") == "الثالث المتوسط" and p.get("branch") == "متوسطة",
        "📋 وزاريات السادس العلمي": lambda p: p.get("grade") == "السادس العلمي" and p.get("branch") == "علمي",
        "📋 وزاريات السادس الأدبي": lambda p: p.get("grade") == "السادس الأدبي" and p.get("branch") == "أدبي",
        "📋 وزاريات السادس المهني": lambda p: p.get("grade") == "السادس المهني" and str(p.get("branch", "")).startswith("مهني /") and p.get("stage") == "المرحلة الثالثة",
        "📖 الخارجي": lambda p: str(p.get("branch", "")).startswith("خارجي"),
    }
    rule = rules.get(kind)
    if rule and any(not rule(p) for p in placements):
        return False
    return True

async def _show_publish_summary(c,state):
    d=await state.get_data()
    placements=await _current_placements(state)
    lines="\n".join("☑️ "+ " › ".join(x for x in [p.get("grade"),p.get("branch"),p.get("department"),p.get("stage"),p.get("subject")] if x) for p in placements)
    await c.message.answer("📌 <b>أماكن النشر المحددة:</b>\n\n"+lines+"\n\nاضغط حفظ.",parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="✅ حفظ في الأقسام المحددة",callback_data="sc_pub_save")],
                                [InlineKeyboardButton(text="⬅️ رجوع",callback_data="sc_pub_back")]
                            ]))

# ---- management: one file vs one placement ----
async def content_list():
    async with async_session_maker() as s:
        return (await s.execute(select(SchoolContent).order_by(SchoolContent.id.desc()).limit(50))).scalars().all()

async def placement_rows(content_id):
    async with async_session_maker() as s:
        return (await s.execute(select(SchoolPlacement).where(SchoolPlacement.content_id==content_id).order_by(SchoolPlacement.id))).scalars().all()

def manage_kb(rows,prefix):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"#{x.id} {x.title[:35]}",callback_data=f"{prefix}:{x.id}")] for x in rows
    ]+[[InlineKeyboardButton(text="🏠 إدارة مدرستي",callback_data="adm_school")]])

@router.callback_query(F.data=="sc_list")
async def sc_list(c):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    rows=await content_list()
    if not rows: return await c.message.answer("📭 لا يوجد محتوى مضاف.",reply_markup=school_owner_menu())
    out="📋 <b>الملفات المخزنة مرة واحدة</b>\n\n"
    for x in rows:
        ps=await placement_rows(x.id)
        media=[]
        if getattr(x,"text_content",None): media.append("📝")
        if getattr(x,"url",None) and not str(x.url).startswith("tgfile:"): media.append("🔗")
        if getattr(x,"photo_id",None): media.append("🖼")
        if getattr(x,"video_id",None): media.append("🎬")
        if getattr(x,"file_id",None): media.append("📎")
        out+=f"#{x.id} • {x.content_type} • {safe_html_escape(x.title)} {' '.join(media)}\n   📍 {len(ps)} مكان نشر\n"
    await c.message.answer(out[:4000],parse_mode="HTML",reply_markup=school_owner_menu()); await c.answer()

@router.callback_query(F.data=="sc_delete_list")
async def sc_delete_list(c):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    rows=await content_list()
    if not rows:return await c.message.answer("📭 لا يوجد محتوى.",reply_markup=school_owner_menu())
    await c.message.answer("🗑 اختر الملف. بعدها يمكنك حذف مكان واحد أو الملف بالكامل:",reply_markup=manage_kb(rows,"sc_del_manage")); await c.answer()

@router.callback_query(F.data.startswith("sc_del_manage:"))
async def sc_del_manage(c):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    cid=int(c.data.split(":")[1])
    async with async_session_maker() as s:x=await s.get(SchoolContent,cid)
    if not x:return await c.answer("غير موجود",show_alert=True)
    ps=await placement_rows(cid)
    rows=[[InlineKeyboardButton(text="🗑 حذف الملف بالكامل",callback_data=f"sc_del_file:{cid}")]]
    for p in ps:
        loc=" › ".join(v for v in [p.grade,p.branch,p.department,p.stage,p.subject] if v)
        rows.append([InlineKeyboardButton(text="🗑 "+loc[:55],callback_data=f"sc_del_place:{p.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ رجوع",callback_data="sc_delete_list")])
    await c.message.answer(f"📄 <b>{safe_html_escape(x.title)}</b>\n\nاحذف مكان نشر فقط أو الملف بالكامل:",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)); await c.answer()

@router.callback_query(F.data.startswith("sc_del_place:"))
async def sc_del_place(c):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    pid=int(c.data.split(":")[1])
    async with async_session_maker() as s:
        p=await s.get(SchoolPlacement,pid)
        if not p:return await c.answer("غير موجود",show_alert=True)
        await s.delete(p);await s.commit()
        cid=p.content_id
    search_engine.invalidate()
    await c.answer("✅ حُذف مكان النشر فقط")
    await sc_del_manage(c)

@router.callback_query(F.data.startswith("sc_del_file:"))
async def sc_del_file(c):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    cid=int(c.data.split(":")[1])
    async with async_session_maker() as s:
        x=await s.get(SchoolContent,cid)
        if not x:return await c.answer("غير موجود",show_alert=True)
        await s.execute(delete(SchoolPlacement).where(SchoolPlacement.content_id==cid))
        await s.delete(x);await s.commit()
    search_engine.invalidate()
    await c.answer("✅ حُذف الملف وكل أماكن نشره")
    await sc_delete_list(c)

@router.callback_query(F.data=="sc_edit_list")
async def sc_edit_list(c):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    rows=await content_list()
    if not rows:return await c.message.answer("📭 لا يوجد محتوى.",reply_markup=school_owner_menu())
    await c.message.answer("✏️ اختر الملف لتعديل أماكن نشره:",reply_markup=manage_kb(rows,"sc_edit_manage")); await c.answer()

@router.callback_query(F.data.startswith("sc_edit_manage:"))
async def sc_edit_manage(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    cid=int(c.data.split(":")[1])
    async with async_session_maker() as s:x=await s.get(SchoolContent,cid)
    if not x:return await c.answer("غير موجود",show_alert=True)
    ps=await placement_rows(cid)
    rows=[]
    for p in ps:
        loc=" › ".join(v for v in [p.grade,p.branch,p.department,p.stage,p.subject] if v)
        rows.append([InlineKeyboardButton(text="☑️ "+loc[:55],callback_data=f"sc_edit_remove:{p.id}")])
    rows += [[InlineKeyboardButton(text="➕ إضافة أماكن جديدة",callback_data=f"sc_edit_add:{cid}")],
             [InlineKeyboardButton(text="⬅️ رجوع",callback_data="sc_edit_list")]]
    await c.message.answer(
        f"✏️ <b>{safe_html_escape(x.title)}</b>\n\n"
        "اضغط مكانًا لإزالته، أو أضف أماكن جديدة. إزالة مكان لا تحذف الملف.",
        parse_mode="HTML",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    ); await c.answer()

@router.callback_query(F.data.startswith("sc_edit_remove:"))
async def sc_edit_remove(c):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    pid=int(c.data.split(":")[1])
    async with async_session_maker() as s:
        p=await s.get(SchoolPlacement,pid)
        if not p:return await c.answer("غير موجود",show_alert=True)
        cid=p.content_id; await s.delete(p); await s.commit()
    search_engine.invalidate()
    await c.answer("تمت إزالة مكان النشر فقط")
    await sc_edit_manage(c,state=None)

@router.callback_query(F.data.startswith("sc_edit_add:"))
async def sc_edit_add(c,state):
    if not is_admin(c.from_user.id): return await c.answer("غير مسموح",show_alert=True)
    cid=int(c.data.split(":")[1])
    async with async_session_maker() as s:x=await s.get(SchoolContent,cid)
    if not x:return await c.answer("غير موجود",show_alert=True)
    await state.update_data(edit_content_id=cid, selected_placements=[])
    await state.set_state(A.school_publish_branch)
    await c.message.answer("➕ اختر الفرع والمرحلة والمادة ثم حدد الأقسام الجديدة.\n\nسيُضاف الربط فقط؛ لن يتم رفع الملف من جديد.",
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                               [InlineKeyboardButton(text="🏭 الصناعي",callback_data="sc_pub_branch:الصناعي")],
                               [InlineKeyboardButton(text="💼 التجاري",callback_data="sc_pub_branch:التجاري")],
                               [InlineKeyboardButton(text="🌾 الزراعي",callback_data="sc_pub_branch:الزراعي")],
                               [InlineKeyboardButton(text="💻 الحاسوب وتقنيات المعلومات",callback_data="sc_pub_branch:الحاسوب وتقنيات المعلومات")]
                           ])); await c.answer()



@router.callback_query(F.data.startswith('tg_purpose:'))
async def tg_purpose(c, state):
    if not is_admin(c.from_user.id): return await c.answer('غير مسموح',show_alert=True)
    purpose=c.data.split(':',1)[1]
    if purpose not in {'university_search','graduation','both'}:
        return await c.answer('خيار غير صالح',show_alert=True)
    d=await state.get_data()
    username=d.get('tg_username','')[:255]
    entity_ref=d.get('tg_entity_ref') or username
    verified=False
    verification_note=''
    try:
        if await telegram_source.start():
            await asyncio.wait_for(telegram_source.client.get_entity(entity_ref), timeout=SEARCH_TIMEOUT)
            verified=True
        else:
            verification_note=' تعذر التحقق من الوصول الآن لأن جلسة Telegram غير مهيأة/غير مصادق عليها.'
    except Exception as exc:
        log = __import__('logging').getLogger(__name__)
        log.warning('Telegram source verification failed for %s: %s', username, exc)
        return await c.answer('تعذر الوصول إلى القناة. تأكد أن الحساب المساعد يستطيع فتحها وأن المعرف صحيح.', show_alert=True)
    async with async_session_maker() as s:
        duplicate=await s.scalar(select(TelegramSource).where(TelegramSource.username==username))
        if duplicate:
            return await c.answer('❌ هذه القناة مضافة مسبقًا.', show_alert=True)
        s.add(TelegramSource(name=d.get('name','Telegram')[:255],
                             username=username,
                             url=d.get('tg_url',''),
                             source_type=purpose))
        await s.commit()
    await record_event(c.from_user.id,'channel_added',metadata={'source':username[:200],'purpose':purpose,'verified':verified})
    search_engine.invalidate(); await state.clear()
    await c.message.answer('✅ تمت إضافة قناة Telegram وربطها بنوع الاستخدام المحدد.' + verification_note,reply_markup=menu())
    await c.answer()


@router.callback_query(F.data.startswith('web_purpose:'))
async def web_purpose(c, state):
    if not is_admin(c.from_user.id): return await c.answer('غير مسموح',show_alert=True)
    purpose=c.data.split(':',1)[1]
    if purpose not in {'university_search','graduation','both'}:
        return await c.answer('خيار غير صالح',show_alert=True)
    d=await state.get_data()
    search_url=d.get('web_search_url','')
    async with async_session_maker() as s:
        duplicate=await s.scalar(select(WebSource).where(WebSource.search_url==search_url))
        if duplicate:
            return await c.answer('❌ هذا الموقع مضاف مسبقًا.', show_alert=True)
        s.add(WebSource(name=d.get('name','الموقع')[:255],
                        base_url=d.get('web_base',''),
                        search_url=search_url,
                        source_type=purpose))
        await s.commit()
    search_engine.invalidate(); await state.clear()
    await c.message.answer('✅ تمت إضافة الموقع وربطه بنوع الاستخدام المحدد.',reply_markup=menu())
    await c.answer()
