from __future__ import annotations
import asyncio
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, or_, select
from database import async_session_maker
from models import SchoolContent, SchoolPlacement, UniversityResource, UniversityProject, User, StudentProfile, TelegramSource, WebSource, UsageEvent
from services.content_index import extract_telegram_document
from services.search_engine import search_engine
from services.usage_service import dashboard
from utils.admin_access import is_admin, is_owner, add_admin, remove_admin, list_admins
from utils.security import safe_html_escape, is_ssrf_safe_url
from urllib.parse import urlparse

router=Router(name='admin_extra')

class AdminExtraStates(StatesGroup):
    book_search=State(); book_rename=State()
    uni_add_type=State(); uni_title=State(); uni_url=State(); uni_meta=State()
    uni_edit=State()
    admin_add=State()


def admin_home_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📚 إدارة الكتب',callback_data='adm_books'), InlineKeyboardButton(text='📄 إدارة الملفات',callback_data='sc_list')],
        [InlineKeyboardButton(text='🎓 إدارة الجامعة',callback_data='adm_university'), InlineKeyboardButton(text='📢 إدارة القنوات',callback_data='adm_tg')],
        [InlineKeyboardButton(text='🌐 المواقع والمصادر',callback_data='adm_web'), InlineKeyboardButton(text='🔎 إعادة الفهرسة',callback_data='adm_reindex')],
        [InlineKeyboardButton(text='👥 المستخدمون',callback_data='adm_users_extra'), InlineKeyboardButton(text='📊 الإحصائيات',callback_data='adm_stats_extra')],
        [InlineKeyboardButton(text='👨‍💼 إدارة المدراء',callback_data='adm_admins')],
        [InlineKeyboardButton(text='🤖 حالة الذكاء الاصطناعي',callback_data='adm_ai_status')],
        [InlineKeyboardButton(text='🏠 الرئيسية',callback_data='main_menu')],
    ])

@router.message(Command('owner'))
async def owner_command(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id): return await m.answer('⛔ غير مسموح.')
    await state.clear(); await m.answer('👑 <b>لوحة المالك</b>\n\nإدارة مركزية للمحتوى والمصادر والمستخدمين والفهرسة.',parse_mode='HTML',reply_markup=admin_home_kb())

@router.callback_query(F.data=='adm_central')
async def central(c,state):
    if not is_admin(c.from_user.id): return await c.answer('غير مسموح',show_alert=True)
    await state.clear(); await c.message.answer('👑 <b>لوحة المالك</b>',parse_mode='HTML',reply_markup=admin_home_kb()); await c.answer()

# ---------------- Books ----------------
async def _books(q=''):
    async with async_session_maker() as s:
        stmt=select(SchoolContent).where(SchoolContent.content_type.in_(['📚 كتب','كتب','كتاب'])).order_by(SchoolContent.updated_at.desc(),SchoolContent.id.desc())
        if q:
            like=f'%{q}%'; stmt=stmt.where(or_(SchoolContent.title.ilike(like),SchoolContent.subject.ilike(like),SchoolContent.content_type.ilike(like)))
        return list((await s.execute(stmt.limit(100))).scalars().all())

def book_row(x):
    return InlineKeyboardButton(text=f'#{x.id} {str(x.title)[:45]}',callback_data=f'book_manage:{x.id}')

async def _book_manage_view(c,cid):
    async with async_session_maker() as s:
        x=await s.get(SchoolContent,cid)
        ps=list((await s.execute(select(SchoolPlacement).where(SchoolPlacement.content_id==cid))).scalars().all()) if x else []
    if not x:return await c.answer('الكتاب غير موجود',show_alert=True)
    rows=[
        [InlineKeyboardButton(text='✏️ تعديل الاسم',callback_data=f'book_rename:{cid}'), InlineKeyboardButton(text='🗑 حذف الكتاب',callback_data=f'book_delete:{cid}')],
        [InlineKeyboardButton(text='🔄 تغيير/إضافة الأقسام',callback_data=f'sc_edit_manage:{cid}')],
        [InlineKeyboardButton(text='⬅️ الكتب',callback_data='adm_books')],
    ]
    locations=[' › '.join(v for v in [p.grade,p.branch,p.department,p.stage,p.subject] if v) for p in ps]
    text=f'📚 <b>{safe_html_escape(x.title)}</b>\n\nالنوع: {safe_html_escape(x.content_type)}\nأماكن النشر: <b>{len(ps)}</b>\n'
    if locations:text+='\n'.join(f'📍 {safe_html_escape(v)}' for v in locations[:10])
    await c.message.answer(text,parse_mode='HTML',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()

@router.callback_query(F.data=='adm_books')
async def adm_books(c,state=None):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    rows=await _books()
    kb=[[book_row(x)] for x in rows]
    kb += [[InlineKeyboardButton(text='➕ إضافة كتاب',callback_data='sc_add:📚 كتب'),InlineKeyboardButton(text='🔎 البحث عن كتاب',callback_data='book_search')],
           [InlineKeyboardButton(text='🏠 لوحة المالك',callback_data='adm_central')]]
    await c.message.answer('📚 <b>إدارة الكتب</b>\n\nالكتاب يمكن أن يظهر في أكثر من قسم ومادة، والاسم قابل للتعديل.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)); await c.answer()

@router.callback_query(F.data=='book_search')
async def book_search(c,state):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    await state.set_state(AdminExtraStates.book_search); await c.message.answer('🔎 اكتب اسم الكتاب أو المادة أو نوع المحتوى:'); await c.answer()

@router.message(AdminExtraStates.book_search,F.text)
async def book_search_text(m,state):
    if not is_admin(m.from_user.id):return
    q=m.text.strip()[:200]; rows=await _books(q)
    kb=[[book_row(x)] for x in rows] or [[InlineKeyboardButton(text='⬅️ الكتب',callback_data='adm_books')]]
    await state.clear(); await m.answer(f'🔎 النتائج: <b>{len(rows)}</b>',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(inline_keyboard=kb));

@router.callback_query(F.data.startswith('book_manage:'))
async def book_manage(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    await _book_manage_view(c,int(c.data.split(':',1)[1]))

@router.callback_query(F.data.startswith('book_rename:'))
async def book_rename(c,state):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    cid=int(c.data.split(':',1)[1])
    async with async_session_maker() as s:x=await s.get(SchoolContent,cid)
    if not x:return await c.answer('غير موجود',show_alert=True)
    await state.set_state(AdminExtraStates.book_rename); await state.update_data(book_id=cid)
    await c.message.answer(f'✏️ الاسم الحالي: <b>{safe_html_escape(x.title)}</b>\n\nأرسل الاسم الجديد:',parse_mode='HTML'); await c.answer()

@router.message(AdminExtraStates.book_rename,F.text)
async def book_rename_text(m,state):
    if not is_admin(m.from_user.id):return
    d=await state.get_data(); cid=int(d.get('book_id',0)); title=m.text.strip()[:500]
    if len(title)<1:return await m.answer('الاسم لا يمكن أن يكون فارغًا.')
    async with async_session_maker() as s:
        x=await s.get(SchoolContent,cid)
        if not x:return await m.answer('الكتاب غير موجود.')
        x.title=title; await s.commit()
    search_engine.invalidate(); await state.clear(); await m.answer('✅ تم تعديل اسم الكتاب فورًا وإزالة نتائج الكاش القديمة.',reply_markup=admin_home_kb())

@router.callback_query(F.data.startswith('book_delete:'))
async def book_delete(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    cid=int(c.data.split(':',1)[1])
    async with async_session_maker() as s:
        x=await s.get(SchoolContent,cid)
        if not x:return await c.answer('غير موجود',show_alert=True)
        # Delete placements first. The Telegram file itself is not deleted from
        # Telegram; if another local storage reference exists it is kept.
        await s.execute(delete(SchoolPlacement).where(SchoolPlacement.content_id==cid))
        await s.delete(x); await s.commit()
    search_engine.invalidate(); await c.answer('تم حذف الكتاب من كل أماكن النشر.'); await adm_books(c)

# ---------------- University CRUD ----------------
def uni_root_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='➕ إضافة مصدر',callback_data='uni_admin_add:resource'),InlineKeyboardButton(text='➕ إضافة مشروع',callback_data='uni_admin_add:project')],
        [InlineKeyboardButton(text='📚 المصادر',callback_data='uni_admin_list:resource'),InlineKeyboardButton(text='🎓 المشاريع',callback_data='uni_admin_list:project')],
        [InlineKeyboardButton(text='⬅️ لوحة المالك',callback_data='adm_central')],
    ])

@router.callback_query(F.data=='adm_university')
async def adm_university(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    await c.message.answer('🎓 <b>إدارة الجامعة</b>\n\nأضف مصادر ومواقع ومشاريع تخرج مرتبطة بالتخصص.',parse_mode='HTML',reply_markup=uni_root_kb()); await c.answer()

@router.callback_query(F.data.startswith('uni_admin_add:'))
async def uni_admin_add(c,state):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    typ=c.data.split(':',1)[1]
    await state.clear(); await state.update_data(uni_kind=typ); await state.set_state(AdminExtraStates.uni_title)
    await c.message.answer(('📚' if typ=='resource' else '🎓')+' أرسل العنوان:'); await c.answer()

@router.message(AdminExtraStates.uni_title,F.text)
async def uni_title(m,state):
    if not is_admin(m.from_user.id):return
    await state.update_data(uni_title=m.text.strip()[:500]); await state.set_state(AdminExtraStates.uni_url); await m.answer('🔗 أرسل الرابط http/https:')

@router.message(AdminExtraStates.uni_url,F.text)
async def uni_url(m,state):
    if not is_admin(m.from_user.id):return
    url=m.text.strip()
    if not is_ssrf_safe_url(url):return await m.answer('❌ الرابط غير صالح أو غير آمن.')
    await state.update_data(uni_url=url); await state.set_state(AdminExtraStates.uni_meta)
    await m.answer('✍️ أرسل البيانات بهذا الشكل: التخصص | السنة | المؤلف/الوصف\nيمكن ترك المؤلف فارغًا.')

@router.message(AdminExtraStates.uni_meta,F.text)
async def uni_meta(m,state):
    if not is_admin(m.from_user.id):return
    d=await state.get_data(); parts=[x.strip() for x in m.text.split('|')]
    major=parts[0][:255] if parts else '' ; year=parts[1][:10] if len(parts)>1 else '' ; third=parts[2] if len(parts)>2 else ''
    async with async_session_maker() as s:
        if d.get('uni_kind')=='resource':
            s.add(UniversityResource(title=d.get('uni_title',''),url=d.get('uni_url',''),major=major,year=year,author=third[:500],description=third[:2000],resource_type='reference',enabled=True))
        else:
            s.add(UniversityProject(title=d.get('uni_title',''),url=d.get('uni_url',''),major=major,year=year,description=third[:2000],enabled=True))
        await s.commit()
    search_engine.invalidate(); await state.clear(); await m.answer('✅ تمت الإضافة ويمكن ظهورها في البحث الجامعي مباشرة.',reply_markup=uni_root_kb())

@router.callback_query(F.data.startswith('uni_admin_list:'))
async def uni_admin_list(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    kind=c.data.split(':',1)[1]
    async with async_session_maker() as s:
        rows=list((await s.execute(select(UniversityResource).order_by(UniversityResource.id.desc()).limit(100))).scalars().all()) if kind=='resource' else list((await s.execute(select(UniversityProject).order_by(UniversityProject.id.desc()).limit(100))).scalars().all())
    out=[]
    for x in rows:
        out.append([InlineKeyboardButton(text=f'#{x.id} {str(x.title)[:45]}',callback_data=f'uni_manage:{kind}:{x.id}')])
    out.append([InlineKeyboardButton(text='⬅️ الجامعة',callback_data='adm_university')])
    await c.message.answer(f'📋 <b>{"المصادر" if kind=="resource" else "مشاريع التخرج"}</b>\nاختر عنصرًا للتعديل أو الحذف.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(inline_keyboard=out)); await c.answer()

@router.callback_query(F.data.startswith('uni_manage:'))
async def uni_manage(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    _,kind,ident=c.data.split(':'); ident=int(ident)
    model=UniversityResource if kind=='resource' else UniversityProject
    async with async_session_maker() as s:x=await s.get(model,ident)
    if not x:return await c.answer('غير موجود',show_alert=True)
    await c.message.answer(
        f'<b>{safe_html_escape(x.title)}</b>\n🔗 {safe_html_escape(x.url or "")}\n📚 التخصص: {safe_html_escape(x.major or "-")}\n📅 {safe_html_escape(x.year or "-")}',
        parse_mode='HTML',reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='✏️ تعديل',callback_data=f'uni_edit:{kind}:{ident}'),InlineKeyboardButton(text='🟢/🔴 تفعيل',callback_data=f'uni_toggle:{kind}:{ident}'),InlineKeyboardButton(text='🗑 حذف',callback_data=f'uni_delete:{kind}:{ident}')],
            [InlineKeyboardButton(text='⬅️ رجوع',callback_data=f'uni_admin_list:{kind}')],
        ])); await c.answer()

@router.callback_query(F.data.startswith('uni_edit:'))
async def uni_edit_start(c,state):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    _,kind,ident=c.data.split(':'); ident=int(ident); model=UniversityResource if kind=='resource' else UniversityProject
    async with async_session_maker() as s:x=await s.get(model,ident)
    if not x:return await c.answer('غير موجود',show_alert=True)
    await state.set_state(AdminExtraStates.uni_edit); await state.update_data(uni_edit_kind=kind,uni_edit_id=ident)
    await c.message.answer(
        f'✏️ <b>تعديل {"المصدر" if kind=="resource" else "مشروع التخرج"}</b>\n\n'
        'أرسل: العنوان | الرابط | التخصص | السنة | الوصف/المؤلف\n'
        'يمكن ترك آخر الحقول فارغة.', parse_mode='HTML'); await c.answer()

@router.message(AdminExtraStates.uni_edit,F.text)
async def uni_edit_save(m,state):
    if not is_admin(m.from_user.id):return
    parts=[x.strip() for x in m.text.split('|')]
    if len(parts)<2 or not parts[0] or not is_ssrf_safe_url(parts[1]):return await m.answer('❌ الصيغة غير صحيحة. استخدم: العنوان | الرابط | التخصص | السنة | الوصف/المؤلف')
    d=await state.get_data(); kind=d.get('uni_edit_kind'); ident=int(d.get('uni_edit_id',0)); model=UniversityResource if kind=='resource' else UniversityProject
    title=parts[0][:500]; url=parts[1][:2000]; major=(parts[2] if len(parts)>2 else '')[:255]; year=(parts[3] if len(parts)>3 else '')[:10]; third='|'.join(parts[4:]).strip()
    async with async_session_maker() as s:
        x=await s.get(model,ident)
        if not x:return await m.answer('العنصر غير موجود.')
        x.title=title; x.url=url; x.major=major; x.year=year
        if kind=='resource':
            x.author=third[:500]; x.description=third[:2000]
        else:
            x.description=third[:2000]
        await s.commit()
    search_engine.invalidate(); await state.clear(); await m.answer('✅ تم تعديل العنصر وإزالة نتائج الكاش القديمة.',reply_markup=uni_root_kb())

@router.callback_query(F.data.startswith('uni_toggle:'))
async def uni_toggle(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    _,kind,ident=c.data.split(':'); model=UniversityResource if kind=='resource' else UniversityProject
    async with async_session_maker() as s:x=await s.get(model,int(ident));
    if not x:return await c.answer('غير موجود',show_alert=True)
    x.enabled=not x.enabled
    async with async_session_maker() as s2:
        y=await s2.get(model,int(ident)); y.enabled=x.enabled; await s2.commit()
    search_engine.invalidate(); await c.answer('تم تغيير الحالة'); await uni_admin_list(c)

@router.callback_query(F.data.startswith('uni_delete:'))
async def uni_delete(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    _,kind,ident=c.data.split(':'); model=UniversityResource if kind=='resource' else UniversityProject
    async with async_session_maker() as s:x=await s.get(model,int(ident));
    if not x:return await c.answer('غير موجود',show_alert=True)
    async with async_session_maker() as s2:y=await s2.get(model,int(ident)); await s2.delete(y); await s2.commit()
    search_engine.invalidate(); await c.answer('تم الحذف'); await uni_admin_list(c)

# ---------------- Persistent admin management ----------------
@router.callback_query(F.data=='adm_admins')
async def adm_admins(c):
    if not is_owner(c.from_user.id):return await c.answer('هذه الصلاحية للمالك فقط.',show_alert=True)
    rows=await list_admins(); out=[]
    for x in rows:
        label=('👑' if x.is_owner else '👨‍💼')+f' {x.telegram_id}'
        if not x.is_owner: out.append([InlineKeyboardButton(text=label,callback_data=f'admin_remove:{x.telegram_id}')])
        else: out.append([InlineKeyboardButton(text=label,callback_data='noop')])
    out += [[InlineKeyboardButton(text='➕ إضافة Admin',callback_data='admin_add')],[InlineKeyboardButton(text='⬅️ لوحة المالك',callback_data='adm_central')]]
    await c.message.answer('👨‍💼 <b>إدارة المدراء</b>\n\nالمالك لا يمكن حذفه من هذه الواجهة.',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(inline_keyboard=out)); await c.answer()

@router.callback_query(F.data=='admin_add')
async def admin_add_start(c,state):
    if not is_owner(c.from_user.id):return await c.answer('للـمالك فقط',show_alert=True)
    await state.set_state(AdminExtraStates.admin_add); await c.message.answer('👤 أرسل Telegram ID للمدير الجديد:'); await c.answer()

@router.message(AdminExtraStates.admin_add,F.text)
async def admin_add_text(m,state):
    if not is_owner(m.from_user.id):return
    try:uid=int(m.text.strip())
    except Exception:return await m.answer('❌ أرسل رقم Telegram ID صحيح.')
    ok,msg=await add_admin(uid,m.from_user.id); await state.clear(); await m.answer(('✅ ' if ok else '⚠️ ')+msg,reply_markup=admin_home_kb())

@router.callback_query(F.data.startswith('admin_remove:'))
async def admin_remove(c):
    if not is_owner(c.from_user.id):return await c.answer('للـمالك فقط',show_alert=True)
    ok,msg=await remove_admin(int(c.data.split(':',1)[1])); await c.answer(msg,show_alert=True); await adm_admins(c)

# ---------------- Reindex ----------------
@router.callback_query(F.data=='adm_reindex')
async def adm_reindex(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    await c.answer('بدأت إعادة الفهرسة...')
    status=await c.message.answer('🔎 <b>إعادة فهرسة المحتوى المحلي</b>\n\nجارٍ فحص ملفات المدرسة...',parse_mode='HTML')
    ok=fail=0
    async with async_session_maker() as s: rows=list((await s.execute(select(SchoolContent).where(SchoolContent.file_id.is_not(None)))).scalars().all())
    for item in rows:
        try:
            text=await extract_telegram_document(c.bot,item.file_id,item.title or 'file',item.mime_type)
            if text:
                async with async_session_maker() as s:
                    x=await s.get(SchoolContent,item.id)
                    if x: x.text_content=text; await s.commit()
                ok+=1
            else: fail+=1
        except Exception: fail+=1
    search_engine.invalidate()
    await status.edit_text(f'✅ <b>اكتملت إعادة الفهرسة</b>\n\n🟢 نجح: {ok}\n🟠 تعذر استخراج النص: {fail}\n\nالعناوين والملفات القابلة للتنزيل تبقى محفوظة حتى عند تعذر استخراج النص.',parse_mode='HTML',reply_markup=admin_home_kb())

# ---------------- Stats and users ----------------
@router.callback_query(F.data=='adm_stats_extra')
async def adm_stats_extra(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    await c.message.answer('📊 اختر الفترة:',reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📅 اليوم',callback_data='stats_range:today'),InlineKeyboardButton(text='📅 أمس',callback_data='stats_range:yesterday')],
        [InlineKeyboardButton(text='📅 آخر 7 أيام',callback_data='stats_range:7d'),InlineKeyboardButton(text='📅 آخر 30 يومًا',callback_data='stats_range:30d')],
        [InlineKeyboardButton(text='📅 هذا الشهر',callback_data='stats_range:month'),InlineKeyboardButton(text='📅 الشهر الماضي',callback_data='stats_range:prev_month')],
        [InlineKeyboardButton(text='📊 كل الوقت',callback_data='stats_range:all')],
        [InlineKeyboardButton(text='⬅️ لوحة المالك',callback_data='adm_central')]
    ])); await c.answer()

@router.callback_query(F.data.startswith('stats_range:'))
async def stats_range(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    key=c.data.split(':',1)[1]; d=await dashboard(key)
    text=(f'📊 <b>الإحصائيات — {key}</b>\n\n👥 إجمالي المستخدمين: {d["users"]}\n🟢 المستخدمون النشطون: {d["active_users"]}\n👤 مستخدمون جدد: {d["new_users"]}\n\n'
          f'🔎 البحث: {d["searches"]}\n📥 الملفات/التحميلات: {d["downloads"]+d["opens"]}\n❓ الأسئلة: {d["questions"]}\n📝 الامتحانات: {d["exams"]}\n💡 شرح الدروس: {d["explanations"]}\n📚 التلخيص: {d["summaries"]}\n📑 طلبات المصادر: {d["sources"]}\n🎓 مشاريع التخرج: {d["graduation_projects"]}\n\n📚 الكتب المضافة: {d["books_added"]}\n📄 الملفات المضافة: {d["files_added"]}\n📢 القنوات المضافة: {d["channels_added"]}')
    await c.message.answer(text,parse_mode='HTML',reply_markup=admin_home_kb()); await c.answer()

@router.callback_query(F.data=='adm_users_extra')
async def adm_users_extra(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    async with async_session_maker() as s: users=list((await s.execute(select(User).order_by(User.last_active.desc()).limit(100))).scalars().all())
    lines=['👥 <b>المستخدمون</b>','']
    for u in users:
        async with async_session_maker() as s:p=await s.scalar(select(StudentProfile).where(StudentProfile.user_id==u.id))
        path=p.path if p else 'غير محدد'
        lines.append(f'🆔 {u.telegram_id} | @{safe_html_escape(u.username or "-")} | {safe_html_escape(path)} | بحث:{u.search_count or 0} | آخر نشاط:{safe_html_escape(str(u.last_active or "-"))}')
    await c.message.answer('\n'.join(lines)[:4000],parse_mode='HTML',reply_markup=admin_home_kb()); await c.answer()

@router.callback_query(F.data=='adm_ai_status')
async def adm_ai_status(c):
    if not is_admin(c.from_user.id):return await c.answer('غير مسموح',show_alert=True)
    from services.ai_service import ai_service
    from config import GEMINI_MODEL,GEMINI_API_KEY
    await c.message.answer(f'🤖 <b>حالة AI</b>\n\nالحالة: {"🟢 متاح" if ai_service.available else "🔴 غير متاح"}\nالنموذج: <code>{safe_html_escape(GEMINI_MODEL or "-")}</code>\nالمفتاح مضبوط: {"نعم" if bool(GEMINI_API_KEY) else "لا"}',parse_mode='HTML',reply_markup=admin_home_kb()); await c.answer()
