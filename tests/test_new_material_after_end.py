from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]

def test_ai_without_session_explicitly_accepts_new_file():
    src=(ROOT/"handlers"/"ai_tutor.py").read_text(encoding="utf-8")
    assert "لا توجد مادة مفتوحة حاليًا" in src
    assert "أرسل الآن <b>الملف الجديد</b>" in src
    assert "ai_pick_school" in src

def test_uploaded_file_replaces_previous_session_authoritatively():
    src=(ROOT/"handlers"/"files.py").read_text(encoding="utf-8")
    assert "replaced_previous_session" in src
    assert "saved = await save_session" in src
    assert "material_ready=True" in src

def test_current_material_never_reads_open_file_history():
    src=(ROOT/"services"/"study_session.py").read_text(encoding="utf-8")
    tail=src[src.index("async def resolve_current_material(uid, session_id: int | None = None):"):]
    assert "UsageEvent" not in tail
    assert "open_file" not in tail
