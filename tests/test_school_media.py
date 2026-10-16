from models import SchoolContent
from handlers.school import CONTENT_LABELS, available_content_types


def test_school_content_has_all_media_fields():
    fields = SchoolContent.__table__.c
    for name in ("text_content", "url", "photo_id", "video_id", "file_id", "mime_type"):
        assert name in fields

def test_new_school_sections_exist():
    assert CONTENT_LABELS["📘 شروحات"] == "شروحات"
    assert "🎓 قبولات" in available_content_types(type("P", (), {"branch":"علمي", "grade":"السادس العلمي"})())
    assert "📘 شروحات" in available_content_types(type("P", (), {"branch":"علمي", "grade":"السادس العلمي"})())


def test_owner_category_normalization_accepts_keyboard_labels():
    from services.school_content import normalize_category, is_owner_library_category
    assert normalize_category("📘 شروحات") == "explanations"
    assert normalize_category("شروحات") == "explanations"
    assert normalize_category("👨‍🏫 مدرسين") == "teachers"
    assert normalize_category("🎓 قبولات") == "admissions"
    assert is_owner_library_category("📘 شروحات")
    assert is_owner_library_category("شروحات")
