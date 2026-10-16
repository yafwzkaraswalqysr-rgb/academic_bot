from __future__ import annotations
import json
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from database import async_session_maker
from config import MAX_STUDY_TEXT_CHARS
from models import User, StudySession, SchoolContent, SchoolPlacement
from services.error_service import log_error

async def get_session(telegram_id: int, session_id: int | None = None):
    """Load the user's authoritative session.

    When a UI action carries an active session id, verify that exact row belongs
    to the Telegram user. This prevents stale buttons/FSM state from resolving
    to another session or to an analytics event.
    """
    async with async_session_maker() as s:
        u = await s.scalar(select(User).where(User.telegram_id == telegram_id))
        if not u:
            return None
        stmt = select(StudySession).where(StudySession.user_id == u.id)
        if session_id is not None:
            try:
                sid = int(session_id)
            except (TypeError, ValueError):
                return None
            stmt = stmt.where(StudySession.id == sid)
        return await s.scalar(stmt)

async def save_session(telegram_id: int, *, title: str, text: str, section: str = "university", content_id: int | None = None,
                       content_type: str | None = None, mode: str | None = None, context: dict | None = None):
    """Persist the one authoritative current-material record for a user.

    This function deliberately has strong success semantics: returning a row means
    the row was committed and can be read back from a fresh DB connection.  A
    missing user or a failed commit is an error, never a silent ``None``.
    """
    text = str(text or '').strip()
    title = str(title or 'مادة').strip() or 'مادة'
    if not text:
        raise ValueError('لا توجد مادة نصية صالحة للحفظ.')
    # The AI layer already chunks/retrieves bounded context.  Keep a generous
    # persistence guard here so normal academic books are not rejected merely
    # because the model-context budget is smaller than the source document.
    if len(text) > MAX_STUDY_TEXT_CHARS:
        raise ValueError('المادة النصية كبيرة جدًا للتخزين الآمن.')

    async with async_session_maker() as s:
        u = await s.scalar(select(User).where(User.telegram_id == telegram_id))
        if not u:
            # All material-backed flows must have a durable User row.  Creating a
            # minimal row here closes the old failure mode where save_session()
            # silently returned None and the caller continued as if saving worked.
            u = User(telegram_id=int(telegram_id))
            s.add(u)
            try:
                await s.flush()
            except IntegrityError:
                await s.rollback()
                u = await s.scalar(select(User).where(User.telegram_id == telegram_id))
                if u is None:
                    raise RuntimeError('تعذر إنشاء سجل المستخدم للمادة الدراسية.')

        uid = u.id
        row = await s.scalar(select(StudySession).where(StudySession.user_id == uid))
        if row is None:
            row = StudySession(user_id=uid, source_title=title[:500], source_text=text, section=section)
            s.add(row)
            try:
                await s.flush()
            except IntegrityError:
                await s.rollback()
                row = await s.scalar(select(StudySession).where(StudySession.user_id == uid))
                if row is None:
                    raise RuntimeError('تعذر إنشاء جلسة المادة الدراسية.')

        row.source_title = title[:500]
        row.source_text = text
        row.section = section
        row.source_content_id = content_id
        row.source_content_type = content_type
        row.current_mode = mode
        payload = dict(context or {})
        payload['material_saved_at'] = datetime.now(timezone.utc).isoformat()
        if content_id is not None:
            payload.setdefault('bookId', content_id)
            payload.setdefault('book_id', content_id)
        row.context_json = json.dumps(payload, ensure_ascii=False)
        row.updated_at = datetime.now(timezone.utc)
        await s.commit()
        await s.refresh(row)

    # Verify persistence through a fresh connection, not the identity map.
    async with async_session_maker() as verify_session:
        verified = await verify_session.scalar(
            select(StudySession).where(StudySession.user_id == uid)
        )
        if verified is None or not (verified.source_text or '').strip():
            raise RuntimeError('تمت محاولة حفظ المادة لكن لم يمكن التحقق من وجودها بعد الحفظ.')
        return verified

async def update_session_mode(telegram_id: int, mode: str, context: dict | None = None):
    async with async_session_maker() as s:
        u = await s.scalar(select(User).where(User.telegram_id == telegram_id))
        if not u:
            return None
        row = await s.scalar(select(StudySession).where(StudySession.user_id == u.id))
        if not row:
            return None
        row.current_mode = mode
        row.updated_at = datetime.now(timezone.utc)
        if context is not None:
            row.context_json = json.dumps(context, ensure_ascii=False)
        await s.commit()
        return row

async def clear_session(telegram_id: int):
    # Ending a study session removes the authoritative current-material row.
    # The analytics event below is retained for statistics only; it is never
    # consulted by resolve_current_material().
    from services.usage_service import record_event
    async with async_session_maker() as s:
        u = await s.scalar(select(User).where(User.telegram_id == telegram_id))
        if not u:
            return
        row = await s.scalar(select(StudySession).where(StudySession.user_id == u.id))
        if row:
            await s.delete(row)
            await s.commit()
    await record_event(telegram_id, 'study_session_ended', metadata={'reason': 'explicit_end_or_change'})


async def _load_session_context(row):
    try:
        return json.loads(row.context_json or '{}') if row.context_json else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        await log_error('study_session.context_parse', 'Invalid StudySession.context_json', user_id=None)
        return {}


async def resolve_current_material(uid, session_id: int | None = None):
    """Return only the explicitly saved current study material.

    Analytics history is not state and can never resurrect a previous material.
    """
    row = await get_session(uid, session_id=session_id) if session_id is not None else await get_session(uid)
    if not row:
        return None

    title = row.source_title or 'مادة'
    text = (row.source_text or '').strip()
    context = await _load_session_context(row)

    # Older StudySession rows may have stored only a SchoolContent ID.
    if row.section == 'school' and not text:
        legacy_content_id = row.source_content_id or context.get('bookId') or context.get('book_id') or context.get('content_id')
        if legacy_content_id:
            try:
                async with async_session_maker() as s:
                    item = await s.scalar(select(SchoolContent).where(SchoolContent.id == int(legacy_content_id)))
                    if item and (item.text_content or '').strip():
                        text = item.text_content.strip()
                        title = item.title or title
                        context.setdefault('bookId', item.id)
                        context.setdefault('book_id', item.id)
                        try:
                            await save_session(
                                uid,
                                title=title,
                                text=text,
                                section='school',
                                content_id=item.id,
                                content_type=item.content_type,
                                mode=row.current_mode,
                                context=context,
                            )
                        except Exception as exc:
                            await log_error('study_session.legacy_recover_save', str(exc), user_id=None)
            except (TypeError, ValueError) as exc:
                await log_error('study_session.legacy_context', f'Invalid content id: {exc}', user_id=None)
            except Exception as exc:
                await log_error('study_session.legacy_context', str(exc), user_id=None)

    if not text:
        await log_error('study_session.empty', f'No source text for session user={uid}', user_id=None)
        return None

    placement_id = context.get('placement_id')
    subject = context.get('subject')
    if row.section == 'school' and row.source_content_id:
        try:
            async with async_session_maker() as s:
                item = await s.scalar(select(SchoolContent).where(SchoolContent.id == row.source_content_id))
                if item:
                    title = item.title or title
                    subject = subject or item.subject or ''
                    if not context.get('content_type'):
                        context['content_type'] = item.content_type
                    if placement_id:
                        placement = await s.scalar(select(SchoolPlacement).where(SchoolPlacement.id == int(placement_id)))
                    else:
                        placement = await s.scalar(
                            select(SchoolPlacement).where(SchoolPlacement.content_id == item.id).order_by(SchoolPlacement.id.desc())
                        )
                    if placement:
                        placement_id = placement.id
                        subject = placement.subject or subject
                        for key in ('stage', 'grade', 'branch', 'department'):
                            if not context.get(key):
                                context[key] = getattr(placement, key, '') or ''
        except Exception as exc:
            await log_error('study_session.context', str(exc), user_id=None)

    from services.material_pages import page_count
    pages = page_count(text)
    return {
        'session_id': getattr(row, 'id', None),
        'title': title,
        'text': text,
        'section': row.section or 'university',
        'content_id': row.source_content_id,
        'book_id': row.source_content_id or context.get('bookId') or context.get('book_id'),
        'content_type': row.source_content_type or context.get('content_type'),
        'placement_id': placement_id,
        'subject': subject or '',
        'mode': row.current_mode,
        'context': context,
        'page_count': pages,
        'has_pages': pages > 0,
    }

