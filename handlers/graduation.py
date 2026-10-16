import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from services.ai_service import ai_service
from services.search_engine import search_engine
from services.user_service import get_user_by_telegram_id, update_user_profile
from database import async_session_maker
from models import StudentProfile
from sqlalchemy import select
from utils.helpers import get_main_menu, get_main_menu_for_user, truncate
from utils.text import clean_ai_text
from utils.security import safe_html_escape
from services.usage_service import record_event
from services.error_service import log_error
from utils.rate_limiter import rate_limiter
from config import AI_TIMEOUT
router=Router()
class GraduationStates(StatesGroup): major=State(); topic=State(); method=State()
GRADUATION_TRIGGER_TEXTS = {'🎓 مشروع التخرج', 'مشروع تخرج', 'مشروع التخرج', 'اريد مشروع تخرج', 'أريد مشروع تخرج', 'اريد مشروع التخرج', 'أريد مشروع التخرج'}

@router.message(F.text.in_(GRADUATION_TRIGGER_TEXTS))
async def grad(m,state):
    u=await get_user_by_telegram_id(m.from_user.id)
    if u:
        async with async_session_maker() as s:
            p=await s.scalar(select(StudentProfile).where(StudentProfile.user_id==u.id))
        if p and p.path=='school':
            return await m.answer('🏫 أنت مسجل كطالب مدرسة. استخدم زر «🏫 مدرستي» للكتب والملازم والملخصات والمدرسين والوزاريات.')
    await state.clear(); await state.set_state(GraduationStates.major)
    await m.answer('🎓 <b>مشروع التخرج</b>\n\nاكتب تخصصك كما هو.\nمثال: هندسة حاسبات، ذكاء اصطناعي، طب أسنان، قانون، محاسبة، فيزياء...')

@router.message(GraduationStates.major,F.text)
async def grad_major(m,state):
    major=m.text.strip()
    if not 2<=len(major)<=200:
        return await m.answer('اكتب تخصصًا بين حرفين و200 حرف.')
    u=await get_user_by_telegram_id(m.from_user.id)
    if u:
        await update_user_profile(u.id, major=major)
    await state.update_data(major=major); await state.set_state(GraduationStates.topic)
    await m.answer(f'🎓 التخصص: <b>{safe_html_escape(major)}</b>\n\nاكتب موضوع مشروع التخرج أو فكرته:',parse_mode='HTML')

@router.message(GraduationStates.topic,F.text)
async def grad_topic(m,state):
    topic=m.text.strip()
    if not 3<=len(topic)<=500: return await m.answer('اكتب موضوعًا بين 3 و500 حرف.')
    d=await state.get_data(); major=d.get('major','غير محدد')
    await state.update_data(topic=topic)
    await state.set_state(GraduationStates.method)
    await m.answer(
        '🎓 <b>اختر طريقة إعداد مشروع التخرج:</b>\n\n'
        '🤖 <b>الذكاء الاصطناعي</b> — تحليل الفكرة وبناء تصور أولي.\n'
        '📡 <b>المصادر</b> — البحث في مصادر مشروع التخرج التي يحددها المالك (Telegram والمواقع).\n'
        '🔎 <b>الاثنان</b> — يجمع بين المصادر والذكاء الاصطناعي.',
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🤖 الذكاء الاصطناعي',callback_data='grad_method:ai')],
            [InlineKeyboardButton(text='📡 المصادر',callback_data='grad_method:telegram')],
            [InlineKeyboardButton(text='🔎 الاثنان',callback_data='grad_method:both')],
            [InlineKeyboardButton(text='🏠 الرئيسية',callback_data='main_menu')]
        ])
    )


@router.callback_query(F.data.startswith('grad_method:'))
async def grad_method(c: CallbackQuery, state: FSMContext):
    method=c.data.split(':',1)[1]
    if method not in {'ai','telegram','both'}:
        return await c.answer('خيار غير صالح',show_alert=True)
    d=await state.get_data(); major=d.get('major','غير محدد'); topic=d.get('topic','')
    if not rate_limiter.is_allowed(c.from_user.id):
        return await c.answer(f'⏳ انتظر {rate_limiter.get_wait_time(c.from_user.id)} ثانية قبل طلب بحث آخر.', show_alert=True)
    status=await c.message.answer('🔎 جاري إعداد نتائج مشروع التخرج...')
    results=[]
    plan=None
    try:
        search_task = None
        ai_task = None
        if method in {'telegram','both'}:
            search_task = asyncio.create_task(asyncio.wait_for(
                search_engine.search(topic, limit=30, mode='fast', major=major, section='university', purpose='graduation'),
                timeout=AI_TIMEOUT,
            ))
        if method in {'ai','both'} and ai_service.available:
            ai_task = asyncio.create_task(asyncio.wait_for(
                ai_service.call(
                    f'حلل فكرة مشروع تخرج تحليلًا أوليًا مختصرًا. التخصص: {major}. '
                    f'الموضوع: {topic}. اقترح المشكلة والأهداف والمنهجية والمخرجات. '
                    'لا تخترع مراجع أو نتائج تجريبية.'
                ), timeout=AI_TIMEOUT
            ))

        tasks = [t for t in (search_task, ai_task) if t is not None]
        if tasks:
            values = await asyncio.gather(*tasks, return_exceptions=True)
            if search_task is not None:
                value = values[tasks.index(search_task)]
                if isinstance(value, Exception):
                    log_error_value = str(value)
                    await log_error('graduation.sources', log_error_value)
                else:
                    results = list(value or [])[:30]
            if ai_task is not None:
                value = values[tasks.index(ai_task)]
                if isinstance(value, Exception):
                    await log_error('graduation.ai', str(value))
                else:
                    plan = value
        lines=[f'🎓 <b>مشروع التخرج</b>',f'التخصص: {safe_html_escape(major)}',f'الموضوع: {safe_html_escape(topic)}','']
        if plan:
            lines += ['🤖 <b>التحليل الأولي</b>',safe_html_escape(truncate(clean_ai_text(plan),3000)),'']
        if method in {'telegram','both'}:
            lines.append(f'📚 المصادر المطابقة: <b>{len(results)}</b>')
        if method == 'ai':
            if plan:
                lines.append('📚 تم إعداد التحليل بالذكاء الاصطناعي دون جلب المصادر الخارجية.')
            else:
                lines.append('🤖 الذكاء الاصطناعي غير متاح حاليًا، لذلك لم يتم توليد التحليل.')
        elif method == 'both' and not ai_service.available:
            lines.append('🤖 الذكاء الاصطناعي غير متاح حاليًا؛ عُرضت نتائج المصادر فقط.')
        for i,r in enumerate(results,1):
            lines.append(f'{i}. {safe_html_escape(truncate(r.get("title"),180))} — {safe_html_escape(truncate(r.get("source_name"),80))}')
        await record_event(c.from_user.id,'graduation',metadata={'major':major[:200],'topic':topic[:250],'count':len(results),'method':method})
        await status.delete()
        await c.message.answer('\n'.join(lines),parse_mode='HTML',reply_markup=await get_main_menu_for_user(c.from_user.id))
        await state.clear()
    except Exception as exc:
        await log_error('graduation.search',str(exc))
        try: await status.edit_text('❌ تعذر إكمال البحث حاليًا. جرّب مرة ثانية.')
        except Exception: pass
        await state.clear()
    await c.answer()
