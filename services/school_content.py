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
