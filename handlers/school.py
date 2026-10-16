from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from database import async_session_maker
from models import StudentProfile, User, SchoolContent, SchoolPlacement
from school_catalog import CONTENT_TYPES, subjects_for
from utils.helpers import get_main_menu_for_user, truncate
from utils.security import safe_html_escape
from services.usage_service import record_event

router = Router(name="school")

CONTENT_LABELS = {
    "📚 كتب": "كتب",
    "📝 ملازم": "ملازم",
    "📌 ملخصات": "ملخصات",
    "👨‍🏫 مدرسين": "مدرسين",
    "📋 وزاريات": "وزاريات",
    "📋 وزاريات السادس المهني": "وزاريات السادس المهني",
    "🎓 قبولات": "قبولات",
    "📘 شروحات": "شروحات",
    "📖 الخارجي": "الخارجي",
}

class SchoolStates(StatesGroup):
    subject = State()


def buttons(items, prefix, columns=2):
    rows = []
    for i in range(0, len(items), columns):
        rows.append([
            InlineKeyboardButton(text=str(x), callback_data=f"{prefix}:{i+j}")
            for j, x in enumerate(items[i:i + columns])
        ])
    rows.append([InlineKeyboardButton(text="🏠 الرئيسية", callback_data="school_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def is_vocational(p: StudentProfile) -> bool:
    return (p.branch or "").strip().startswith("مهني /")


def available_content_types(p: StudentProfile) -> list[str]:
    """School is an owner-managed library. Nothing here calls an external search."""
    items = ["📚 كتب", "📝 ملازم", "📌 ملخصات", "👨‍🏫 مدرسين", "📘 شروحات", "📖 الدراسة الحرة", "📖 الخارجي"]
    if is_vocational(p):
        items += ["📋 وزاريات السادس المهني"]
    items += ["🎓 قبولات"]
    # Ministerial content for the school grades supported by the catalog.
    if (p.grade or "") in {"السادس ابتدائي", "الثالث متوسط", "السادس العلمي", "السادس الأدبي"}:
        items.append("📋 وزاريات")
    return items


async def profile_for(telegram_id: int):
    async with async_session_maker() as s:
        user = await s.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            return None
        return await s.scalar(select(StudentProfile).where(StudentProfile.user_id == user.id))


def _norm_value(value: str | None) -> str:
    """Normalize labels for reliable matching across owner/student UI labels."""
    import re
    v=(value or "").strip()
    v=v.replace("‏","").replace("‎","").replace("ـ","")
    v=re.sub(r"^[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\s]+", "", v).strip()
    for prefix in ("🏫 ","📚 ","🛠 ","📖 ","📋 ","📝 "):
        v=v.replace(prefix,"").strip()
    v=re.sub(r"^(?:مهني|خارجي)\s*/\s*", "", v).strip()
    return v

def _tokens(value: str | None) -> set[str]:
    v=_norm_value(value)
    if not v: return set()
    return {v, v.replace("/", " ").strip()}

def _branch_value(p: StudentProfile) -> str:
    return _norm_value(p.branch)

def _grade_values(p: StudentProfile) -> set[str]:
    values={_norm_value(x) for x in (p.grade,p.stage) if _norm_value(x)}
    if _norm_value(p.grade) in {"المرحلة الأولى","المرحلة الثانية","المرحلة الثالثة"}:
        values.add("السادس المهني")
    return values

def _kind_aliases(kind: str) -> set[str]:
    raw=(kind or "").strip()
    label=CONTENT_LABELS.get(raw, raw)
    aliases={raw,label,_norm_value(raw),_norm_value(label)}
    try:
        from services.school_content import normalize_category, SCHOOL_LIBRARY_CATEGORIES
        normalized=normalize_category(raw)
        if normalized in SCHOOL_LIBRARY_CATEGORIES:
            aliases.add(SCHOOL_LIBRARY_CATEGORIES[normalized])
            aliases.add(normalized)
    except Exception:
        pass
    if raw == "📋 وزاريات السادس المهني":
        aliases |= {"📋 وزاريات","وزاريات","وزاريات السادس المهني"}
    return aliases

def _same(a: str | None, *vals: str | None) -> bool:
    aa=_norm_value(a)
    if not aa: return True
    return any(aa==_norm_value(v) for v in vals if _norm_value(v))

def _placement_matches(p: StudentProfile, placement: SchoolPlacement, subject: str | None) -> bool:
    """Match an owner placement against the student's full path.

    Older owner records may store vocational stage/grade in the opposite
    hierarchy fields, so vocational paths accept both representations.
    """
    ps=_norm_value(p.stage); pg=_norm_value(p.grade); pb=_norm_value(p.branch)
    pd=_norm_value(p.department); psub=_norm_value(subject)
    s=_norm_value(placement.stage); g=_norm_value(placement.grade)
    b=_norm_value(placement.branch); d=_norm_value(placement.department); sub=_norm_value(placement.subject)

    if sub and psub and sub != psub: return False

    # Stage/grade: normal school paths and vocational paths use different
    # labels; allow the student's grade/stage interchange for compatibility.
    if s and not _same(s, ps, pg):
        return False
    if g and not _same(g, pg, ps, "السادس المهني" if pg in {"المرحلة الأولى","المرحلة الثانية","المرحلة الثالثة"} else None):
        return False

    if b:
        bclean=_norm_value(b)
        pclean=_norm_value(pb)
        # Exact branch, vocational branch, or external branch path.
        if bclean != pclean:
            bparts={_norm_value(x) for x in bclean.split("/") if _norm_value(x)}
            pparts={_norm_value(x) for x in pclean.split("/") if _norm_value(x)}
            if not bparts.issubset(pparts | {pclean}):
                return False

    if d and d not in {pd,"عام"}: return False
    return True

async def _owner_items(p: StudentProfile, kind: str, subject: str | None = None):
    """Return every owner-published file matching the student's full path."""
    aliases=_kind_aliases(kind)
    async with async_session_maker() as s:
        rows=(await s.execute(
            select(SchoolContent, SchoolPlacement)
            .join(SchoolPlacement, SchoolPlacement.content_id==SchoolContent.id)
            # Published placements are the source of truth. Older records may
            # have a missing/false shared flag even though the owner created a
            # placement for students. Do not hide those published records.
            .where(True)
            .order_by(SchoolContent.id.desc(), SchoolPlacement.id.desc())
        )).all()
    matched=[]; seen=set()
    for item, placement in rows:
        item_types={_norm_value(item.content_type)}
        if not (item_types & {_norm_value(x) for x in aliases}):
            continue
        if not _placement_matches(p, placement, subject):
            continue
        if item.id not in seen:
            matched.append(item); seen.add(item.id)
    return matched

async def _send_items(c: CallbackQuery, p: StudentProfile, kind: str, subject: str | None = None) -> bool:
    items = await _owner_items(p, kind, subject)
    if not items:
        return False

    label = CONTENT_LABELS.get(kind, kind)
    title = f"📦 <b>{safe_html_escape(label)}</b>"
    if subject:
        title += f"\n📖 المادة: <b>{safe_html_escape(subject)}</b>"
    await c.message.answer(title, parse_mode="HTML")

    for item in items:
        caption = f"📄 <b>{safe_html_escape(item.title)}</b>"
        if item.teacher:
            caption += f"\n👨‍🏫 {safe_html_escape(item.teacher)}"
        if getattr(item, "text_content", None):
            caption += f"\n\n{safe_html_escape(item.text_content)}"
        sent = False
        if getattr(item, "photo_id", None):
            try:
                await c.message.answer_photo(item.photo_id, caption=caption[:1024], parse_mode="HTML"); sent = True
            except Exception:
                pass
        if getattr(item, "video_id", None):
            try:
                await c.message.answer_video(item.video_id, caption=caption[:1024], parse_mode="HTML"); sent = True
            except Exception:
                pass
        if item.file_id:
            try:
                await c.message.answer_document(item.file_id, caption=caption[:1024], parse_mode="HTML"); sent = True
            except Exception:
                pass
        public_url = item.url if item.url and not str(item.url).startswith("tgfile:") else None
        if public_url:
            await c.message.answer(caption + f"\n\n🔗 {safe_html_escape(public_url)}", parse_mode="HTML"); sent = True
        if not sent:
            await c.message.answer(caption, parse_mode="HTML")
        if getattr(item, 'text_content', None):
            await c.message.answer('🤖 استخدم هذه المادة مع أدوات الدراسة:', reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='🤖 سؤال وجواب / امتحان / شرح / تلخيص', callback_data=f'study_content:{item.id}')],
                [InlineKeyboardButton(text='⬅️ مدرستي', callback_data='school_home')]
            ]))
        await record_event(c.from_user.id, 'open_file', metadata={'school_content_id': item.id, 'content_type': item.content_type})
    return True


@router.message(F.text == "🏫 مدرستي")
async def school_home(m: Message, state: FSMContext):
    p = await profile_for(m.from_user.id)
    if not p:
        return await m.answer("ابدأ بـ /start واختر طالب مدرسة أولاً.")
    await state.clear()
    text = "🏫 <b>مدرستي</b>\n\n"
    text += f"المرحلة: {safe_html_escape(p.stage or '-')}\n"
    text += f"الصف: {safe_html_escape(p.grade or '-')}"
    if p.branch:
        text += f"\nالفرع: {safe_html_escape(p.branch)}"
    if p.department:
        text += f"\nالقسم: {safe_html_escape(p.department)}"
    text += "\n\nاختر نوع المحتوى الذي أضافه المالك:"
    await m.answer(text, parse_mode="HTML", reply_markup=buttons(available_content_types(p), "school_type"))


@router.callback_query(F.data == "school_home")
async def school_home_cb(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.answer("🏠 الرئيسية", reply_markup=await get_main_menu_for_user(c.from_user.id))
    await c.answer()


@router.callback_query(F.data.startswith("school_type:"))
async def school_type(c: CallbackQuery, state: FSMContext):
    p = await profile_for(c.from_user.id)
    if not p:
        return await c.answer("ابدأ بـ /start", show_alert=True)
    available = available_content_types(p)
    try:
        i = int(c.data.split(":", 1)[1])
        kind = available[i]
    except Exception:
        return await c.answer("خيار غير صالح", show_alert=True)

    # No external search in any school section. The owner controls the data.
    if kind in {"🎓 قبولات", "📋 وزاريات السادس المهني", "📖 الدراسة الحرة"}:
        await c.answer()
        found = await _send_items(c, p, kind)
        if not found:
            await c.message.answer("📭 لا يوجد محتوى مضاف حاليًا لهذا القسم.")
        return

    # Subjects are just navigation. Actual files still come only from owner content.
    branch = (p.branch or "").strip()
    vocational = branch.split("/", 1)[1].strip() if branch.startswith("مهني /") else ""
    dept = p.department or ""
    subjects = subjects_for(
        p.stage or "", p.grade or "", branch, vocational, dept,
        p.grade if p.grade in {"المرحلة الأولى", "المرحلة الثانية", "المرحلة الثالثة"} else "",
    ) or ["عام"]
    await state.update_data(content_type=kind, subjects=subjects)
    await state.set_state(SchoolStates.subject)
    await c.message.answer(f"{kind}\n\nاختَر المادة:", reply_markup=buttons(subjects, "school_subject"))
    await c.answer()


@router.callback_query(F.data.startswith("school_subject:"))
async def school_subject(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    subjects = data.get("subjects", [])
    try:
        i = int(c.data.split(":", 1)[1])
        subject = subjects[i]
    except Exception:
        return await c.answer("خيار غير صالح", show_alert=True)
    kind = data.get("content_type", "📚 كتب")
    p = await profile_for(c.from_user.id)
    if not p:
        return await c.answer("ابدأ بـ /start", show_alert=True)

    await c.answer()
    found = await _send_items(c, p, kind, subject)
    if not found:
        await c.message.answer(
            f"📭 لا يوجد محتوى مضاف حاليًا في <b>{safe_html_escape(kind)}</b> لمادة <b>{safe_html_escape(subject)}</b>.\n\n"
            "سيظهر هنا عندما يضيفه المالك من لوحة المالك.",
            parse_mode="HTML",
        )
    await state.clear()

# Owner-managed school library includes: books, notes, teachers, summaries, admissions, ministerial, free_study.
