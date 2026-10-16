"""School content categories managed entirely by the owner.

No external search is performed by this module. Files/links are stored as
owner-published content and can be routed to the selected student section.
"""
FREE_STUDY_CATEGORY = "free_study"

SCHOOL_LIBRARY_CATEGORIES = {
    "books": "📚 كتب",
    "notes": "📝 ملازم",
    "teachers": "👨‍🏫 مدرسين",
    "summaries": "📌 ملخصات",
    "admissions": "🎓 قبولات",
    "explanations": "📘 شروحات",
    "ministerial": "📋 وزاريات",
    "free_study": "📖 الدراسة الحرة",
}

def is_owner_library_category(category: str) -> bool:
    return normalize_category(category) in SCHOOL_LIBRARY_CATEGORIES

def normalize_category(category: str) -> str:
    aliases = {
        "ملزمة": "notes", "ملازم": "notes",
        "كتاب": "books", "كتب": "books",
        "ملخص": "summaries", "ملخصات": "summaries",
        "مدرس": "teachers", "مدرسين": "teachers",
        "قبول": "admissions", "قبولات": "admissions",
        "شرح": "explanations", "شروحات": "explanations",
        "وزاريات": "ministerial", "وزارية": "ministerial",
        "الدراسة الحرة": "free_study", "دراسة حرة": "free_study",
    }
    raw = (category or "").strip()
    # Accept the exact labels used by the owner/student keyboards as well as
    # plain Arabic aliases.
    labels = {label: key for key, label in SCHOOL_LIBRARY_CATEGORIES.items()}
    if raw in labels:
        return labels[raw]
    if raw in SCHOOL_LIBRARY_CATEGORIES:
        return raw
    return aliases.get(raw, aliases.get(raw.replace("📚 ", "").replace("📝 ", "").replace("📌 ", "").replace("👨‍🏫 ", "").replace("📘 ", "").replace("🎓 ", "").replace("📋 ", "").replace("📖 ", ""), raw))


def normalize_path_value(value: str | None) -> str:
    """Normalize owner/student school-path labels for matching.

    Keeps Arabic grade/subject text intact while removing UI emoji, vocational
    prefixes and presentation-only marks.
    """
    import re
    from school_catalog import normalize_school_grade
    v = (value or '').strip()
    v = v.replace('\u200f', '').replace('\u200e', '').replace('ـ', '')
    v = re.sub(r'^[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\s]+', '', v).strip()
    for prefix in ("🏫 ", "📚 ", "🛠 ", "📖 ", "📋 ", "📝 "):
        v = v.replace(prefix, '').strip()
    v = re.sub(r'^(?:مهني|خارجي)\s*/\s*', '', v).strip()
    return normalize_school_grade(v)


def placement_matches_profile(profile, placement, subject: str | None = None) -> bool:
    """Return whether an owner publication matches a student's school path.

    Vocational onboarding stores "المرحلة الأولى/الثانية/الثالثة" in the
    profile's `grade`, while owner placements store "السادس المهني" in `grade`
    and the vocational phase in `stage`. Both representations must match.
    """
    ps = normalize_path_value(getattr(profile, 'stage', ''))
    pg = normalize_path_value(getattr(profile, 'grade', ''))
    pb = normalize_path_value(getattr(profile, 'branch', ''))
    pd = normalize_path_value(getattr(profile, 'department', ''))
    psub = normalize_path_value(subject)
    s = normalize_path_value(getattr(placement, 'stage', ''))
    g = normalize_path_value(getattr(placement, 'grade', ''))
    b = normalize_path_value(getattr(placement, 'branch', ''))
    d = normalize_path_value(getattr(placement, 'department', ''))
    sub = normalize_path_value(getattr(placement, 'subject', ''))

    if sub and psub and sub != psub:
        return False
    vocational_phases = {normalize_path_value(x) for x in ('المرحلة الأولى', 'المرحلة الثانية', 'المرحلة الثالثة')}
    if s and s not in {ps, pg}:
        return False
    if g:
        if g == normalize_path_value('السادس المهني') and (pg in vocational_phases or ps in vocational_phases):
            pass
        elif g not in {pg, ps}:
            return False
    if b and pb:
        if b != pb:
            bparts = {normalize_path_value(x) for x in b.split('/') if normalize_path_value(x)}
            pparts = {normalize_path_value(x) for x in pb.split('/') if normalize_path_value(x)}
            if not bparts.issubset(pparts | {pb}):
                return False
    if d and d not in {pd, normalize_path_value('عام')}:
        return False
    return True
