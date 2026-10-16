from __future__ import annotations
import json
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from database import async_session_maker
from models import StudentProfile, User, Search, Result
from services.search_engine import search_engine
from services.user_service import get_or_create_user
from services.usage_service import record_event
from services.error_service import log_error
from utils.helpers import get_main_menu_for_user, truncate, chunk_text
from utils.security import safe_html_escape
from config import MAX_QUERY_LENGTH, RESULTS_PER_PAGE

router = Router(name='university')

class UniversityStates(StatesGroup):
    root = State()
    resource_query = State()
    project_query = State()
    custom_resource_limit = State()
    custom_project_limit = State()
    results = State()


def menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📚 المصادر الجامعية', callback_data='uni_resources'), InlineKeyboardButton(text='🎓 مشاريع التخرج', callback_data='uni_projects')],
        [InlineKeyboardButton(text='🌐 بحث الجامعة', callback_data='uni_search')],
        [InlineKeyboardButton(text='🏠 الرئيسية', callback_data='main_menu')],
    ])


def limit_kb(prefix):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔢 10', callback_data=f'{prefix}:10'), InlineKeyboardButton(text='🔢 20', callback_data=f'{prefix}:20'), InlineKeyboardButton(text='🔢 50', callback_data=f'{prefix}:50')],
        [InlineKeyboardButton(text='🔢 100', callback_data=f'{prefix}:100'), InlineKeyboardButton(text='♾️ غير محدود', callback_data=f'{prefix}:0')],
        [InlineKeyboardButton(text='✏️ عدد مخصص', callback_data=f'{prefix}:custom')],
        [InlineKeyboardButton(text='🏠 الرئيسية', callback_data='main_menu')],
    ])

async def _university_user(tg_id):
    u = await get_or_create_user(tg_id)
    async with async_session_maker() as s:
        p = await s.scalar(select(StudentProfile).where(StudentProfile.user_id == u.id))
    return u, p


def _filter_type(results, project: bool):
    out=[]
    for r in results:
        t=str(r.get('source_type') or '')
        if project:
            if t == 'university_project' or 'project' in t or 'مشروع' in str(r.get('source_name') or ''):
                out.append(r)
        else:
            if t != 'university_project':
                out.append(r)
    return out

async def _persist_results(tg_id, user, results, query, kind):
    async with async_session_maker() as s:
        rec=Search(user_id=user.id, query=query, mode='university', scope=kind, section='university', result_count=len(results))
        s.add(rec); await s.flush()
        for r in results:
            s.add(Result(search_id=rec.id, source_name=r.get('source_name',''), source_type=r.get('source_type',''),
                         title=r.get('title','بدون عنوان'), url=r.get('url'), author=r.get('author'), year=str(r.get('year') or ''),
                         snippet=r.get('snippet'), file_url=r.get('file_url'), is_downloadable=bool(r.get('is_downloadable')),
                         tg_chat=r.get('tg_chat'), tg_message_id=r.get('tg_message_id'), metadata_json=json.dumps(r.get('metadata',{}),ensure_ascii=False),
                         rank_score=int(r.get('rank_score',0) or 0)))
        await s.commit()
        ids=list((await s.execute(select(Result.id).where(Result.search_id==rec.id).order_by(Result.rank_score.desc(), Result.id))).scalars().all())
    await record_event(tg_id, 'university_sources' if kind == 'sources' else 'graduation', user_id=user.id, metadata={'query':query[:250],'count':len(results)})
    return rec.id, ids

async def _show_page(m, ids, page, heading, kind):
    total=max(1,(len(ids)+RESULTS_PER_PAGE-1)//RESULTS_PER_PAGE)
    page=max(0,min(int(page),total-1)); start=page*RESULTS_PER_PAGE; chunk=ids[start:start+RESULTS_PER_PAGE]
    async with async_session_maker() as s:
        rows=(await s.execute(select(Result).where(Result.id.in_(chunk)))).scalars().all(); by={x.id:x for x in rows}
    lines=[f'<b>{safe_html_escape(heading)}</b>',f'📄 الصفحة <b>{page+1}</b> من <b>{total}</b>',f'🔎 العدد المتوفر: <b>{len(ids)}</b>','']
    for i,rid in enumerate(chunk,start+1):
        r=by.get(rid)
        if not r: continue
        lines.append(f'<b>{i}. {safe_html_escape(truncate(r.title,240))}</b>')
        if r.author: lines.append(f'👤 {safe_html_escape(truncate(r.author,140))}')
        if r.year: lines.append(f'📅 {safe_html_escape(r.year)}')
        if r.snippet: lines.append(f'📝 {safe_html_escape(truncate(r.snippet,280))}')
        if r.url: lines.append(f'🔗 {safe_html_escape(truncate(r.url,500))}')
        lines.append('')
    nav=[]
    if page>0: nav.append(InlineKeyboardButton(text='⬅️ السابق',callback_data=f'uni_page:{kind}:{page-1}'))
    nav.append(InlineKeyboardButton(text=f'📄 {page+1} من {total}',callback_data='noop'))
    if page<total-1: nav.append(InlineKeyboardButton(text='التالي ➡️',callback_data=f'uni_page:{kind}:{page+1}'))
    kb=InlineKeyboardMarkup(inline_keyboard=[nav,[InlineKeyboardButton(text='🏠 الرئيسية',callback_data='main_menu'),InlineKeyboardButton(text='🎓 الجامعة',callback_data='uni_home')]])
    parts = chunk_text('\n'.join(lines), 3500)
    for i, part in enumerate(parts):
        await m.answer(part,parse_mode='HTML',reply_markup=kb if i == len(parts)-1 else None)

@router.message(F.text == '🎓 الجامعة')
async def university_home(m: Message, state: FSMContext):
    u,p=await _university_user(m.from_user.id)
    if p and p.path == 'school':
        return await m.answer('🏫 أنت مسجل كطالب مدرسة. افتح «🏫 مدرستي» لمحتواك المدرسي.')
    await state.clear(); await state.set_state(UniversityStates.root)
    await m.answer('🎓 <b>الجامعة</b>\n\nالتخصص: '+safe_html_escape(u.major or 'غير محدد')+'\nالمرحلة: '+safe_html_escape(u.study_level or 'غير محددة')+'\n\nاختر ما تريد البحث عنه:',parse_mode='HTML',reply_markup=menu_kb())

@router.callback_query(F.data == 'uni_home')
async def uni_home(c,state):
    await state.clear(); await university_home(c.message,state); await c.answer()

@router.callback_query(F.data == 'uni_resources')
async def uni_resources(c,state):
    await state.set_state(UniversityStates.resource_query)
    await c.message.answer('📚 <b>المصادر الجامعية</b>\n\nاكتب التخصص أو الموضوع أو اسم المصدر:',parse_mode='HTML'); await c.answer()

@router.message(UniversityStates.resource_query,F.text)
async def resource_query(m,state):
    q=m.text.strip()
    if len(q)<2 or len(q)>MAX_QUERY_LENGTH: return await m.answer(f'اكتب بحثًا بين حرفين و{MAX_QUERY_LENGTH} حرفًا.')
    await state.update_data(query=q,kind='sources')
    await state.set_state(UniversityStates.custom_resource_limit)
    await m.answer('🔎 كم مصدرًا تريد؟ لا يوجد حد ثابت 100؛ سيُعرض المتاح فعليًا.',reply_markup=limit_kb('uni_res_count'))

@router.callback_query(F.data.startswith('uni_res_count:'))
async def resource_count(c,state):
    val=c.data.split(':',1)[1]
    if val=='custom':
        await state.set_state(UniversityStates.custom_resource_limit)
        await c.message.answer('✏️ اكتب العدد المطلوب مثل 250.')
        return await c.answer()
    await state.update_data(limit=int(val)); await _run_university_search(c.message,state,project=False); await c.answer()

@router.message(UniversityStates.custom_resource_limit,F.text)
async def resource_custom_count(m,state):
    try:n=int(m.text.strip())
    except Exception:n=-1
    if n<1: return await m.answer('❌ اكتب عددًا صحيحًا أكبر من صفر.')
    await state.update_data(limit=n); await _run_university_search(m,state,project=False)

@router.callback_query(F.data == 'uni_projects')
async def uni_projects(c,state):
    await state.set_state(UniversityStates.project_query)
    await c.message.answer('🎓 <b>مشاريع التخرج</b>\n\nاكتب موضوع المشروع أو التخصص:',parse_mode='HTML'); await c.answer()

@router.message(UniversityStates.project_query,F.text)
async def project_query(m,state):
    q=m.text.strip()
    if len(q)<2 or len(q)>MAX_QUERY_LENGTH: return await m.answer(f'اكتب بحثًا بين حرفين و{MAX_QUERY_LENGTH} حرفًا.')
    await state.update_data(query=q,kind='projects')
    await state.set_state(UniversityStates.custom_project_limit)
    await m.answer('🎓 كم مشروعًا تريد؟',reply_markup=limit_kb('uni_proj_count'))

@router.callback_query(F.data.startswith('uni_proj_count:'))
async def project_count(c,state):
    val=c.data.split(':',1)[1]
    if val=='custom':
        await state.set_state(UniversityStates.custom_project_limit); await c.message.answer('✏️ اكتب العدد المطلوب مثل 100.'); return await c.answer()
    await state.update_data(limit=int(val)); await _run_university_search(c.message,state,project=True); await c.answer()

@router.message(UniversityStates.custom_project_limit,F.text)
async def project_custom_count(m,state):
    try:n=int(m.text.strip())
    except Exception:n=-1
    if n<1:return await m.answer('❌ اكتب عددًا صحيحًا أكبر من صفر.')
    await state.update_data(limit=n); await _run_university_search(m,state,project=True)

@router.callback_query(F.data == 'uni_search')
async def uni_search(c,state):
    from handlers.search import begin as begin_search
    await begin_search(c.message, state)
    await c.answer()

async def _run_university_search(m,state,project=False):
    d=await state.get_data(); q=d.get('query',''); limit=int(d.get('limit',10) or 0)
    u,p=await _university_user(m.from_user.id)
    status=await m.answer('🔎 جاري جمع النتائج وترتيبها...')
    try:
        results=await search_engine.search(q,limit=limit,mode='fast',major=u.major,level=u.study_level,section='university',profile=p)
        results=_filter_type(results,project)
        if limit and len(results)>limit: results=results[:limit]
        sid,ids=await _persist_results(m.from_user.id,u,results,q,'projects' if project else 'sources')
        await state.update_data(result_ids=ids,page=0,heading=('🎓 مشاريع التخرج' if project else '📚 المصادر الجامعية'),kind=('projects' if project else 'sources'))
        await state.set_state(UniversityStates.results)
        try: await status.delete()
        except Exception: pass
        if not ids: return await m.answer('لم أجد نتائج متاحة حاليًا. لن يتم اختراع نتائج غير موجودة.')
        await _show_page(m,ids,0,'🎓 مشاريع التخرج' if project else '📚 المصادر الجامعية','projects' if project else 'sources')
    except Exception as exc:
        await log_error('university.search',str(exc),user_id=u.id)
        try: await status.edit_text('❌ تعذر تنفيذ البحث الجامعي. تم تسجيل الخطأ للمطور.')
        except Exception: pass

@router.callback_query(F.data.startswith('uni_page:'))
async def uni_page(c,state):
    try:
        _,kind,page=c.data.split(':'); page=int(page)
    except Exception:return await c.answer('صفحة غير صالحة',show_alert=True)
    d=await state.get_data(); ids=d.get('result_ids') or []
    if not ids:return await c.answer('انتهت الجلسة.',show_alert=True)
    await c.answer(); await _show_page(c.message,ids,page,d.get('heading') or ('🎓 مشاريع التخرج' if kind=='projects' else '📚 المصادر الجامعية'),kind)
