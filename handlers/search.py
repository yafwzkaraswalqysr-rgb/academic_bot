from __future__ import annotations
import json
import logging
from aiogram import Router, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from database import async_session_maker
from models import Search, Result, StudentProfile, User
from services.search_engine import search_engine
from services.user_service import get_or_create_user
from services.usage_service import record_event
from services.error_service import log_error
from utils.helpers import get_main_menu_for_user, get_mode_keyboard, get_scope_keyboard, get_date_keyboard, get_limit_keyboard, get_pagination_keyboard, truncate, chunk_text
from utils.security import safe_html_escape
from config import MAX_QUERY_LENGTH, RESULTS_PER_PAGE
from utils.rate_limiter import rate_limiter

router = Router(name='search')
log = logging.getLogger(__name__)


class SearchStates(StatesGroup):
    mode = State()
    query = State()
    scope = State()
    date = State()
    year = State()
    limit = State()
    custom_limit = State()
    results = State()


async def _profile(user_id: int):
    async with async_session_maker() as s:
        u = await s.scalar(select(User).where(User.telegram_id == user_id))
        if not u:
            return None
        return u, await s.scalar(select(StudentProfile).where(StudentProfile.user_id == u.id))


@router.message(F.text == '🔎 بحث')
async def begin(m, state):
    u, profile = await _profile(m.from_user.id) or (await get_or_create_user(m.from_user.id, m.from_user.username, m.from_user.full_name), None)
    if not rate_limiter.is_allowed(m.from_user.id):
        return await m.answer(f'⏳ انتظر {rate_limiter.get_wait_time(m.from_user.id)} ثانية قبل بحث جديد.')
    section = 'school' if profile and profile.path == 'school' else 'university'
    await state.clear()
    await state.update_data(section=section, profile_path=getattr(profile, 'path', None))
    await state.set_state(SearchStates.mode)
    await m.answer('اختر وضع البحث:', reply_markup=get_mode_keyboard())


@router.message(SearchStates.query, F.text)
async def query_text(m, state):
    q = m.text.strip()
    if len(q) < 2 or len(q) > MAX_QUERY_LENGTH:
        return await m.answer(f'اكتب بحثًا بين حرفين و{MAX_QUERY_LENGTH} حرف.')
    await state.update_data(query=q)
    data = await state.get_data()
    if data.get('section') == 'school':
        await state.set_state(SearchStates.limit)
        return await m.answer('🏫 البحث المدرسي يبحث في المواد والكتب والملازم والمحتوى المضاف من المالك. كم نتيجة تريد؟', reply_markup=get_limit_keyboard())
    await state.set_state(SearchStates.scope)
    await m.answer('اختر نطاق البحث:', reply_markup=get_scope_keyboard())


@router.message(SearchStates.year, F.text)
async def year_text(m, state):
    raw = m.text.strip()
    y = int(raw) if raw.isdigit() and 1900 <= int(raw) <= 2100 else None
    await state.update_data(year=str(y) if y else None)
    await state.set_state(SearchStates.limit)
    await m.answer('كم نتيجة تريد؟', reply_markup=get_limit_keyboard())


@router.message(SearchStates.custom_limit, F.text)
async def custom_limit(m, state):
    try:
        n = int(m.text.strip())
    except Exception:
        n = 0
    if n <= 0:
        return await m.answer('❌ اكتب عددًا صحيحًا أكبر من صفر، مثل 250.')
    await state.update_data(limit=n)
    await run_search(m, state)


async def run_search(m, state):
    d = await state.get_data()
    u = await get_or_create_user(m.from_user.id, m.from_user.username, m.from_user.full_name)
    async with async_session_maker() as s:
        profile = await s.scalar(select(StudentProfile).where(StudentProfile.user_id == u.id))
    section = d.get('section') or ('school' if profile and profile.path == 'school' else 'university')
    status = await m.answer('🔎 جاري البحث...')
    try:
        results = await search_engine.search(
            d['query'], d.get('scope'), d.get('date'), d.get('year'), d.get('limit', 10),
            d.get('mode', 'fast'), u.major, u.study_level, section=section, profile=profile
        )
        async with async_session_maker() as s:
            rec = Search(
                user_id=u.id,
                query=d['query'],
                mode=d.get('mode', 'fast'),
                scope=d.get('scope'),
                year=str(d.get('year') or ''),
                section=section,
                result_count=len(results),
            )
            s.add(rec)
            await s.flush()
            for r in results:
                s.add(Result(
                    search_id=rec.id,
                    source_name=r.get('source_name', ''),
                    source_type=r.get('source_type', ''),
                    title=r.get('title', 'بدون عنوان'),
                    url=r.get('url'),
                    author=r.get('author'),
                    year=str(r.get('year') or ''),
                    snippet=r.get('snippet'),
                    is_downloadable=r.get('is_downloadable', False),
                    file_url=r.get('file_url'),
                    tg_chat=r.get('tg_chat'),
                    tg_message_id=r.get('tg_message_id'),
                    metadata_json=json.dumps(r.get('metadata', {}), ensure_ascii=False),
                    rank_score=int(r.get('rank_score', 0) or 0),
                ))
            u.search_count = (u.search_count or 0) + 1
            s.add(u)
            await s.commit()
            ids = list((await s.execute(select(Result.id).where(Result.search_id == rec.id).order_by(Result.rank_score.desc(), Result.id))).scalars().all())
        await record_event(m.from_user.id, 'search', user_id=u.id, metadata={'section': section, 'query': d['query'][:250], 'count': len(results)})
        await state.update_data(search_id=rec.id, result_ids=ids, page=0, section=section)
        await state.set_state(SearchStates.results)
        try:
            await status.delete()
        except Exception:
            pass
        if not ids:
            msg = '🏫 لم أجد محتوى محليًا مطابقًا.' if section == 'school' else 'لم أجد نتائج مناسبة.'
            return await m.answer(msg, reply_markup=await get_main_menu_for_user(m.from_user.id))
        await show_page(m, ids, 0)
    except Exception as exc:
        await log_error('search.run_search', str(exc), user_id=u.id)
        try:
            await status.edit_text('❌ حدث خطأ أثناء البحث. تم تسجيل الخطأ للمطور، جرّب مرة أخرى.')
        except Exception:
            await m.answer('❌ حدث خطأ أثناء البحث. تم تسجيل الخطأ للمطور.')


async def show_page(m, ids, page):
    total_pages = max(1, (len(ids) + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE)
    page = max(0, min(int(page), total_pages - 1))
    start = page * RESULTS_PER_PAGE
    chunk = ids[start:start + RESULTS_PER_PAGE]
    if not chunk:
        return await m.answer('لا توجد نتائج في هذه الصفحة.')
    lines = [
        '📚 <b>نتائج البحث</b>',
        f'📄 الصفحة <b>{page + 1}</b> من <b>{total_pages}</b>',
        f'🔎 إجمالي النتائج المحفوظة: <b>{len(ids)}</b>',
        '',
    ]
    async with async_session_maker() as s:
        rows = (await s.execute(select(Result).where(Result.id.in_(chunk)))).scalars().all()
        by_id = {r.id: r for r in rows}
    for n, rid in enumerate(chunk, start + 1):
        r = by_id.get(rid)
        if not r:
            continue
        lines.append(
            f'\n<b>{n}. {safe_html_escape(truncate(r.title, 220))}</b>\n'
            f'📚 {safe_html_escape(truncate(r.source_name, 100))}\n'
            f'👤 {safe_html_escape(truncate(r.author or "غير متوفر", 120))}\n'
            f'📅 {safe_html_escape(r.year or "غير متوفر")}'
        )
        if r.snippet:
            lines.append(f'\n📝 {safe_html_escape(truncate(r.snippet, 350))}')
        if r.url:
            lines.append(f'\n🔗 {safe_html_escape(truncate(r.url, 500))}')
        lines.append('')
    lines.append('🤲 نسألكم الدعاء لمالك البوت وللمؤمنين والمؤمنات')
    await m.answer(
        ''.join(lines),
        parse_mode='HTML',
        reply_markup=get_pagination_keyboard(page, total_pages, prefix='page')
    )
