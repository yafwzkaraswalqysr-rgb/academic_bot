from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from config import MAX_FILE_SIZE, SUPPORTED_EXTENSIONS, TEMP_DIR
from database import async_session_maker
from models import User, StudentProfile
from services.file_service import extract_text
from services.ai_service import ai_service
from services.user_service import get_or_create_user
from sqlalchemy import select
from utils.helpers import truncate
from utils.security import safe_html_escape
from handlers.ai_tutor import AITutorStates, mode_keyboard
from services.study_session import save_session
from services.usage_service import record_event
from services.error_service import log_error

router=Router(name="files")
logger=logging.getLogger(__name__)
MAX_ANALYSIS_CHARS=30000
DOWNLOAD_RETRIES=3

async def download_telegram_file(bot, file_path: str, destination: Path):
    """Download to disk with retries; avoids keeping the whole document in RAM."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    last=None
    for attempt in range(1, DOWNLOAD_RETRIES+1):
        try:
            destination.unlink(missing_ok=True)
            await bot.download_file(file_path, destination=destination, timeout=90, chunk_size=64*1024)
            if not destination.exists() or destination.stat().st_size == 0:
                raise RuntimeError("Telegram أرجع ملفًا فارغًا.")
            return
        except Exception as exc:
            last=exc
            logger.warning("Telegram file download attempt %s/%s failed: %s",attempt,DOWNLOAD_RETRIES,exc)
            destination.unlink(missing_ok=True)
            if attempt < DOWNLOAD_RETRIES:
                await asyncio.sleep(attempt * 1.5)
    raise RuntimeError(f"فشل تنزيل الملف من Telegram بعد {DOWNLOAD_RETRIES} محاولات: {last}")


def file_actions():
    return mode_keyboard()

@router.message(F.document)
async def document(m: Message, state: FSMContext):
    # Owner PDF uploads during button creation are published content, not AI input.
    current_state = await state.get_state()
    if current_state and (current_state.endswith(':button_content') or current_state.endswith(':school_file')):
        from handlers.admin import process_admin_document, process_owner_school_document
        if current_state.endswith(':school_file'):
            if await process_owner_school_document(m, state):
                return
        else:
            if await process_admin_document(m, state):
                return
    doc=m.document
    if not doc:return
    filename=doc.file_name or "academic_file"
    ext=Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return await m.answer("❌ صيغة الملف غير مدعومة.\n\nالمسموح: 📄 PDF · 📝 DOCX · 📊 PPTX · 📃 TXT")
    if doc.file_size and doc.file_size>MAX_FILE_SIZE:
        return await m.answer(f"❌ الملف كبير جدًا ({doc.file_size/1024/1024:.1f} MB). الحد: {MAX_FILE_SIZE/1024/1024:.0f} MB.")

    status=await m.answer("📥 جارٍ تنزيل الملف...\n🔁 إذا انقطع الاتصال سأعيد المحاولة تلقائيًا.")
    path=TEMP_DIR / f"upload_{m.from_user.id}_{m.message_id}{ext}"
    try:
        tg_file=None; last_get=None
        for attempt in range(1,4):
            try:
                tg_file=await m.bot.get_file(doc.file_id)
                break
            except Exception as exc:
                last_get=exc
                if attempt<3: await asyncio.sleep(attempt)
        if not tg_file or not tg_file.file_path:
            raise RuntimeError(f"تعذر الحصول على مسار الملف من Telegram: {last_get}")
        await download_telegram_file(m.bot,tg_file.file_path,path)
        if path.stat().st_size>MAX_FILE_SIZE: raise RuntimeError("الملف بعد التنزيل تجاوز الحد المسموح.")

        await status.edit_text("📝 تم تنزيل الملف. جارٍ استخراج النص فقط...\nلا يوجد اختبار أو تحليل تلقائي الآن.")
        try:
            text=await extract_text(str(path))
        except ValueError as local_exc:
            # Scanned PDFs often contain only images. Use Gemini only as an OCR
            # fallback; never auto-summarize or auto-generate an exam.
            if ext==".pdf" and "OCR" in str(local_exc).upper() and ai_service.available:
                await status.edit_text("🧾 هذا PDF ممسوح كصور. جارٍ استخراج النص منه فقط...")
                text=await ai_service.extract_document(str(path),"application/pdf")
                if not text: raise local_exc
            else:
                raise
        text=(text or "").strip()
        if not text: raise RuntimeError("لم أجد نصًا داخل الملف.")

        user=await get_or_create_user(m.from_user.id,m.from_user.username,m.from_user.full_name)
        async with async_session_maker() as session:
            db_user=await session.scalar(select(User).where(User.id==user.id))
            if db_user:
                db_user.upload_count=(db_user.upload_count or 0)+1
                await session.commit()

        # Persist the extracted material in the database. FSM only tracks the
        # current UI state, so restart does not destroy the material context.
        section = 'school'
        async with async_session_maker() as ss:
            profile = await ss.scalar(select(StudentProfile).join(User, User.id == StudentProfile.user_id).where(User.telegram_id == m.from_user.id))
        if not profile:
            section = 'university'
        await save_session(m.from_user.id, title=filename, text=text, section=section)
        await record_event(m.from_user.id, 'file_added', metadata={'extension': ext, 'chars': len(text)})
        await state.clear(); await state.set_state(AITutorStates.mode)
        preview=safe_html_escape(truncate(text,3200))
        if len(text)>3200: preview += "\n\n… تم اختصار المعاينة. المادة الكاملة محفوظة للجلسة."
        await status.edit_text(
            "✅ <b>تم استخراج النص بنجاح</b>\n\n"
            f"📄 الملف: <b>{safe_html_escape(filename)}</b>\n"
            f"📝 الأحرف المستخرجة: <b>{len(text):,}</b>\n\n"
            f"<b>المادة المستخرجة:</b>\n{preview}\n\n"
            "اختَر الآن ماذا تريد أن أفعل بالمادة:",parse_mode="HTML",reply_markup=file_actions())
    except Exception as exc:
        logger.exception("File processing failed for user %s",m.from_user.id)
        reason=str(exc).strip() or "سبب غير معروف"
        await log_error('file.document', reason, user_id=None)
        await status.edit_text("❌ <b>تعذر معالجة الملف</b>\n\n"+safe_html_escape(truncate(reason,1000))+"\n\nجرّب إرسال الملف مرة ثانية. إذا كان PDF ممسوحًا كصور، استخدم صورة الصفحة مباشرة.",parse_mode="HTML")
    finally:
        path.unlink(missing_ok=True)
