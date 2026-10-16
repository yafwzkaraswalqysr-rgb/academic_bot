from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from services.ai_service import ai_service
from services.search_engine import search_engine
from services.user_service import get_user_by_telegram_id
from database import async_session_maker
from models import StudentProfile
from sqlalchemy import select
from utils.helpers import get_main_menu, get_main_menu_for_user, truncate
from utils.text import clean_ai_text
from utils.security import safe_html_escape
from services.usage_service import record_event
from services.error_service import log_error
router=Router()
class GraduationStates(StatesGroup): major=State(); topic=State()
@router.message(F.text=='🎓 مشروع التخرج')
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
    await state.update_data(major=major); await state.set_state(GraduationStates.topic)
    await m.answer(f'🎓 التخصص: <b>{major}</b>\n\nاكتب موضوع مشروع التخرج أو فكرته:',parse_mode='HTML')

@router.message(GraduationStates.topic,F.text)
async def grad_topic(m,state):
    topic=m.text.strip()
    if not 3<=len(topic)<=500: return await m.answer('اكتب موضوعًا بين 3 و500 حرف.')
    d=await state.get_data(); major=d.get('major','غير محدد')
    await m.answer('🔎 جاري البحث عن مصادر أكاديمية مرتبطة بالمشروع...')
    results=await search_engine.search(topic,limit=0,mode='fast',major=major,section='university')
    await record_event(m.from_user.id,'graduation',metadata={'major':major[:200],'topic':topic[:250],'count':len(results)})
    lines=[f'🎓 <b>مشروع التخرج</b>',f'التخصص: {major}',f'الموضوع: {topic}','']
    if ai_service.available:
        try:
            plan=await ai_service.call(f'أنشئ خطة أولية مختصرة لمشروع تخرج. التخصص: {major}. الموضوع: {topic}. لا تخترع مراجع.')
        except Exception as exc:
            await log_error('graduation.ai',str(exc))
            plan=None
        if plan: lines += ['🤖 <b>اقتراح أولي</b>',safe_html_escape(truncate(clean_ai_text(plan),2500)),'']
    lines.append(f'📚 المصادر المطابقة: {len(results)}')
    for i,r in enumerate(results,1): lines.append(f'{i}. {truncate(r.get("title"),180)} — {truncate(r.get("source_name"),80)}')
    await m.answer('\n'.join(lines),parse_mode='HTML',reply_markup=await get_main_menu_for_user(m.from_user.id)); await state.clear()
