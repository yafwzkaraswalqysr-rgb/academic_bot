from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup
from sqlalchemy import select
from database import async_session_maker
from models import StudentProfile

from school_catalog import VOCATIONAL_BRANCHES, school_grades
from services.student_service import save_student_profile
from services.user_service import get_or_create_user, get_user_by_telegram_id, update_user_profile
from utils.helpers import get_main_menu_for_user

router=Router(name="start")

SCHOOL_STAGES=["🏫 ابتدائية","🏫 متوسطة","🏫 إعدادية"]
PREP_BRANCHES=["علمي","أدبي","مهني"]
VOCATIONAL_STAGES=["المرحلة الأولى","المرحلة الثانية","المرحلة الثالثة"]

class OnboardingStates(StatesGroup):
    path=State(); university_level=State(); university_major=State()
    school_stage=State(); school_grade=State(); prep_branch=State(); vocational_stage=State(); vocational_branch=State(); vocational_department=State()
    free_study=State()

def kb(items,prefix,columns=2):
    rows=[]
    for i in range(0,len(items),columns):
        rows.append([InlineKeyboardButton(text=str(x),callback_data=f"{prefix}:{i+j}") for j,x in enumerate(items[i:i+columns])])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def home_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🎓 طالب جامعي"),KeyboardButton(text="🏫 طالب مدرسة")],[KeyboardButton(text="📚 دراسة حرة")]],resize_keyboard=True,one_time_keyboard=True)

def finish_text(): return "🤲 نسألكم الدعاء لمالك البوت وللمؤمنين والمؤمنات"

async def _start(m,state):
    await state.clear()
    user = await get_or_create_user(m.from_user.id,m.from_user.username,m.from_user.full_name)
    async with async_session_maker() as s:
        profile = await s.scalar(select(StudentProfile).where(StudentProfile.user_id == user.id))
    if profile:
        await m.answer(
            "👋 <b>أهلاً بك من جديد</b>\n\nاختر ما تريد مباشرة:",
            parse_mode='HTML',
            reply_markup=await get_main_menu_for_user(m.from_user.id),
        )
        return
    await m.answer("📚 <b>أهلاً بك في المساعد الأكاديمي الشامل</b>\n\n✨ هذا البوت مجاني.\n\n🎓 اختر نوع دراستك:",parse_mode="HTML",reply_markup=home_kb())
    await state.set_state(OnboardingStates.path)

@router.message(CommandStart())
async def start_command(m,state): await _start(m,state)
@router.message(Command("start"))
async def start_fallback(m,state): await _start(m,state)

@router.message(OnboardingStates.path,F.text=="🎓 طالب جامعي")
async def university(m,state):
    await state.set_state(OnboardingStates.university_level)
    await m.answer("🎓 اختر مرحلتك الجامعية:",reply_markup=kb(["أولى","ثانية","ثالثة","رابعة","خامسة","سادسة","دراسات عليا"],"ulevel"))

@router.callback_query(F.data.startswith("ulevel:"))
async def ulevel(c,state):
    vals=["أولى","ثانية","ثالثة","رابعة","خامسة","سادسة","دراسات عليا"]
    try:i=int(c.data.split(":",1)[1]); val=vals[i]
    except Exception:return await c.answer("خيار غير صالح",show_alert=True)
    await state.update_data(university_level=val); await state.set_state(OnboardingStates.university_major)
    await c.message.answer("✍️ اكتب تخصصك أو مجالك:"); await c.answer()

@router.message(OnboardingStates.university_major,F.text)
async def umajor(m,state):
    d=await state.get_data(); uid=m.from_user.id; u=await get_or_create_user(uid,m.from_user.username,m.from_user.full_name)
    major=m.text.strip()[:150]; level=d.get("university_level","")
    await update_user_profile(u.id,study_level=level,major=major)
    await save_student_profile(u.id,path="university",stage=level,subject=major)
    await state.clear(); await m.answer("✅ تم حفظ بياناتك الجامعية.\n\nوجهتك الآن هي قسم الطالب الجامعي: البحث، الدراسة، مشروع التخرج، والمكتبة.\n\n"+finish_text(),reply_markup=await get_main_menu_for_user(uid))

@router.message(OnboardingStates.path,F.text=="🏫 طالب مدرسة")
async def school(m,state):
    await state.set_state(OnboardingStates.school_stage); await m.answer("🏫 اختر المرحلة الدراسية:",reply_markup=kb(SCHOOL_STAGES,"sstage"))

@router.callback_query(F.data.startswith("sstage:"))
async def sstage(c,state):
    try:i=int(c.data.split(":",1)[1]); val=SCHOOL_STAGES[i]
    except Exception:return await c.answer("خيار غير صالح",show_alert=True)
    await state.update_data(school_stage=val)
    if val=="🏫 إعدادية":
        await state.set_state(OnboardingStates.prep_branch)
        await c.message.answer("📚 اختر الفرع:",reply_markup=kb(PREP_BRANCHES,"pbranch"))
    else:
        grades=school_grades(val)
        await state.set_state(OnboardingStates.school_grade)
        await c.message.answer("🎓 اختر الصف:",reply_markup=kb(grades,"sgrade"))
    await c.answer()

@router.callback_query(F.data.startswith("sgrade:"))
async def sgrade(c,state):
    d=await state.get_data()
    grades=school_grades(d.get("school_stage",""))
    try:i=int(c.data.split(":",1)[1]); grade=grades[i]
    except Exception:return await c.answer("خيار غير صالح",show_alert=True)
    await state.update_data(school_grade=grade)
    if d.get("school_stage")=="🏫 إعدادية":
        await state.set_state(OnboardingStates.prep_branch)
        await c.message.answer("📚 اختر الفرع:",reply_markup=kb(PREP_BRANCHES,"pbranch"))
    else:
        await finish_school(c.message,state,grade,user_id=c.from_user.id)
    await c.answer()

@router.callback_query(F.data.startswith("pbranch:"))
async def pbranch(c,state):
    vals=PREP_BRANCHES
    try:i=int(c.data.split(":",1)[1]); branch=vals[i]
    except Exception:return await c.answer("خيار غير صالح",show_alert=True)
    await state.update_data(prep_branch=branch)
    if branch=="مهني":
        await state.set_state(OnboardingStates.vocational_stage)
        await c.message.answer("🛠 اختر المرحلة المهنية:",reply_markup=kb(VOCATIONAL_STAGES,"vstage"))
    else:
        await state.set_state(OnboardingStates.school_grade)
        await c.message.answer("🎓 اختر الصف:",reply_markup=kb(["الرابع العلمي","الخامس العلمي","السادس العلمي"] if branch=="علمي" else ["الرابع الأدبي","الخامس الأدبي","السادس الأدبي"],"pgrade"))
    await c.answer()

@router.callback_query(F.data.startswith("pgrade:"))
async def pgrade(c,state):
    d=await state.get_data(); branch=d.get("prep_branch")
    grades=["الرابع العلمي","الخامس العلمي","السادس العلمي"] if branch=="علمي" else ["الرابع الأدبي","الخامس الأدبي","السادس الأدبي"]
    try:i=int(c.data.split(":",1)[1]); grade=grades[i]
    except Exception:return await c.answer("خيار غير صالح",show_alert=True)
    await state.update_data(school_grade=grade); await finish_school(c.message,state,grade,user_id=c.from_user.id); await c.answer()

@router.callback_query(F.data.startswith("vstage:"))
async def vstage(c,state):
    try:i=int(c.data.split(":",1)[1]); stage=VOCATIONAL_STAGES[i]
    except Exception:return await c.answer("خيار غير صالح",show_alert=True)
    await state.update_data(vocational_stage=stage); await state.set_state(OnboardingStates.vocational_branch)
    await c.message.answer("🛠 اختر الفرع المهني:",reply_markup=kb(VOCATIONAL_BRANCHES,"vbranch")); await c.answer()

@router.callback_query(F.data.startswith("vbranch:"))
async def vbranch(c,state):
    try:i=int(c.data.split(":",1)[1]); branch=VOCATIONAL_BRANCHES[i]
    except Exception:return await c.answer("خيار غير صالح",show_alert=True)
    await state.update_data(vocational_branch=branch)
    from school_catalog import VOCATIONAL_DEPARTMENTS
    depts=VOCATIONAL_DEPARTMENTS.get(branch,[])
    await state.set_state(OnboardingStates.vocational_department)
    await c.message.answer("📂 اختر القسم:",reply_markup=kb(depts+["عام"],"vdept")); await c.answer()

@router.callback_query(F.data.startswith("vdept:"))
async def vdept(c,state):
    d=await state.get_data()
    from school_catalog import VOCATIONAL_DEPARTMENTS
    branch=d.get("vocational_branch",""); depts=VOCATIONAL_DEPARTMENTS.get(branch,[])+["عام"]
    try:i=int(c.data.split(":",1)[1]); dept=depts[i]
    except Exception:return await c.answer("خيار غير صالح",show_alert=True)
    await state.update_data(vocational_department=dept)
    await finish_school(c.message,state,d.get("vocational_stage",""),vocational=True,user_id=c.from_user.id); await c.answer()

async def finish_school(m,state,grade,vocational=False,user_id=None):
    d=await state.get_data()
    # When called from a callback query, m is the bot's outgoing Message, so
    # m.from_user is the bot, not the student. Always use the callback user's
    # Telegram ID passed by the caller.
    uid = user_id if user_id is not None else m.from_user.id
    u = await get_user_by_telegram_id(uid)
    if u is None:
        u = await get_or_create_user(uid, None, None)
    stage=d.get("school_stage",""); branch=d.get("prep_branch","")
    if vocational:
        branch="مهني / "+d.get("vocational_branch","")
        department=d.get("vocational_department","")
    else:
        department=""
    await save_student_profile(u.id,path="school",stage=stage,grade=grade,branch=branch,department=department)
    await update_user_profile(u.id,study_level=f"مدرسة: {stage} / {grade}",major=department or branch or "مدرسة")
    await state.clear()
    await m.answer("✅ <b>تم حفظ بياناتك المدرسية.</b>\n\n🏫 افتح <b>مدرستي</b> للوصول إلى محتوى مرحلتك وفرعك وقسمك.",parse_mode="HTML",reply_markup=await get_main_menu_for_user(uid))

@router.message(OnboardingStates.path,F.text=="📚 دراسة حرة")
async def free(m,state): await state.set_state(OnboardingStates.free_study); await m.answer("📚 اكتب المرحلة أو المادة التي تريد الدراسة الحرة فيها:")

@router.message(OnboardingStates.free_study,F.text)
async def free_text(m,state):
    u=await get_or_create_user(m.from_user.id,m.from_user.username,m.from_user.full_name); subject=m.text.strip()[:150]
    await update_user_profile(u.id,study_level="دراسة حرة",major=subject); await save_student_profile(u.id,path="free",subject=subject)
    await state.clear(); await m.answer("✅ تم حفظ بيانات الدراسة الحرة.\n\nوجهتك الآن هي أدوات الدراسة والبحث الحر.\n\n"+finish_text(),reply_markup=await get_main_menu_for_user(m.from_user.id))

@router.message(F.text == "🏠 القائمة الرئيسية")
async def home_text(m, state):
    await state.clear()
    await m.answer("🏠 <b>القائمة الرئيسية</b>", parse_mode="HTML", reply_markup=await get_main_menu_for_user(m.from_user.id))

@router.message(Command("help"))
@router.message(F.text=="ℹ️ المساعدة")
async def help_(m):
    await m.answer("📚 <b>المساعد الأكاديمي</b>\n\n🎓 الجامعي: بحث، دراسة، مشروع تخرج، مكتبة.\n🏫 المدرسة: كتب، ملازم، ملخصات، مدرسين، وزاريات.\n📚 الدراسة الحرة: بحث ودراسة بدون مسار مدرسي.\n🤖 الذكاء الاصطناعي: سؤال، شرح، تلخيص، واختبارات من المادة التي ترسلها.\n\n"+finish_text(),parse_mode="HTML",reply_markup=await get_main_menu_for_user(m.from_user.id))
