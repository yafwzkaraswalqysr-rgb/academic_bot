from __future__ import annotations
from pathlib import Path
from uuid import uuid4
import asyncio
import logging
from config import TEMP_DIR, MAX_FILE_SIZE, FILE_PROCESS_TIMEOUT
from services.file_service import extract_text

log = logging.getLogger(__name__)

async def extract_telegram_document(bot, file_id: str, filename: str, mime_type: str | None = None) -> str:
    """Download a Telegram document temporarily, extract its text, then remove it."""
    ext = Path(filename or 'file').suffix.lower()
    if not file_id:
        return ''
    tmp = TEMP_DIR / f'index_{uuid4().hex}{ext or ".bin"}'
    try:
        tg_file = await bot.get_file(file_id)
        if not getattr(tg_file, 'file_path', None):
            raise RuntimeError('Telegram لم يُرجع مسار الملف.')
        await bot.download_file(tg_file.file_path, destination=tmp, timeout=90, chunk_size=64 * 1024)
        if not tmp.exists() or tmp.stat().st_size <= 0:
            raise RuntimeError('الملف المُنزّل فارغ.')
        if tmp.stat().st_size > MAX_FILE_SIZE:
            raise ValueError('حجم الملف أكبر من الحد المسموح.')
        try:
            return (await asyncio.wait_for(extract_text(str(tmp)), timeout=FILE_PROCESS_TIMEOUT)).strip()
        except ValueError as exc:
            # Keep OCR as an optional fallback for scanned PDFs, matching the
            # normal student file path. If AI is not configured, keep the file
            # searchable by title/metadata rather than rejecting publication.
            if ext == '.pdf' and 'OCR' in str(exc).upper():
                try:
                    from services.ai_service import ai_service
                    if ai_service.available:
                        text = await ai_service.extract_document(str(tmp), 'application/pdf')
                        return (text or '').strip()
                except Exception:
                    log.exception('Owner PDF OCR fallback failed')
            raise
    finally:
        tmp.unlink(missing_ok=True)
