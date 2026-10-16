from __future__ import annotations

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import TEMP_DIR
from handlers.ai_tutor import AITutorStates, mode_keyboard
from services.ai_service import ai_service
from utils.helpers import get_main_menu_for_user, truncate
from utils.security import safe_html_escape
from services.study_session import save_session
from services.usage_service import record_event
from services.error_service import log_error
from services.user_service import get_or_create_user
from database import async_session_maker
from models import User, StudentProfile
from sqlalchemy import select

router=Router(name="voice_image")
log=logging.getLogger(__name__)

@router.message(F.voice)
async def voice(m: Message):
    await m.answer("🎙️ التفريغ الصوتي غير مفعّل في هذه النسخة بعد. أرسل النص أو صورة المادة أو ملف PDF/DOCX/PPTX/TXT.")

@router.message(F.photo)
async def photo(m: Message, state: FSMContext):
    if not ai_service.available:
        return await m.answer("❌ الذكاء الاصطناعي غير متاح حاليًا. تأكد من GEMINI_API_KEY.")
    status=await m.answer("🖼️ جارٍ استخراج النص من الصورة فقط...\nلا يوجد بحث أو اختبار تلقائي.")
    tmp=TEMP_DIR/f"academic_image_{m.from_user.id}_{m.message_id}.jpg"
    try:
        await m.bot.download(m.photo[-1],destination=tmp)
        extracted=await ai_service.analyze_image(
            tmp.read_bytes(),
            "استخرج كل النص الأكاديمي الظاهر في الصورة كما هو قدر الإمكان. حافظ على ترتيب العناوين والأسئلة والنقاط. لا تحل الأسئلة ولا تلخص ولا تضف معلومات. إذا كانت الصورة مخططًا أو جدولًا، صف محتواه نصيًا باختصار. أعد النص فقط.",
            mime_type="image/jpeg",
        )
        extracted=(extracted or "").strip()
        if not extracted: raise RuntimeError("لم أستطع استخراج نص واضح من الصورة.")
        u=await get_or_create_user(m.from_user.id,m.from_user.username,m.from_user.full_name)
        async with async_session_maker() as ss:
            profile=await ss.scalar(select(StudentProfile).where(StudentProfile.user_id==u.id))
        section='school' if profile and profile.path=='school' else 'university'
        await save_session(m.from_user.id, title='صورة المادة', text=extracted, section=section)
        await record_event(m.from_user.id, 'file_added', metadata={'extension': '.jpg', 'chars': len(extracted), 'type': 'image'})
        await state.clear(); await state.set_state(AITutorStates.mode)
        preview=safe_html_escape(truncate(extracted,3500))
        if len(extracted)>3500: preview += "\n\n… تم اختصار المعاينة. المادة الكاملة محفوظة للجلسة."
        await status.edit_text(
            "✅ <b>تم استخراج المادة من الصورة</b>\n\n"
            "📝 <b>النص المستخرج:</b>\n"+preview+"\n\n"
            "اختَر الآن ماذا تريد أن أفعل بالمادة:",parse_mode="HTML",reply_markup=mode_keyboard())
    except Exception as exc:
        log.exception("Image extraction failed")
        await log_error('file.image', str(exc), user_id=None)
        await status.edit_text("❌ <b>تعذر استخراج النص من الصورة</b>\n\n"+safe_html_escape(str(exc)),parse_mode="HTML",reply_markup=await get_main_menu_for_user(m.from_user.id))
    finally:
        tmp.unlink(missing_ok=True)
