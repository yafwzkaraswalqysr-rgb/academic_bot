from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_school_listing_does_not_auto_replace_current_material():
    src = (ROOT / 'handlers' / 'school.py').read_text(encoding='utf-8')
    start = src.index('async def _send_items')
    end = src.index('\n\n@router.message(F.text == "🏫 مدرستي")', start)
    block = src[start:end]
    assert 'await save_session(' not in block
    assert "callback_data=f'study_content:{item.id}:{placement.id if placement else \"\"}'" in block
    assert '📚 جعلها المادة الحالية' in block


def test_usage_events_are_not_a_current_material_store():
    src = (ROOT / 'services' / 'study_session.py').read_text(encoding='utf-8')
    assert 'UsageEvent' not in src[src.index('async def resolve_current_material'):]
    assert '_latest_opened_book' not in src


def test_study_session_has_additive_legacy_migrations():
    src = (ROOT / 'database.py').read_text(encoding='utf-8')
    assert "'study_sessions': [" in src
    assert "('source_content_id', 'INTEGER')" in src
    assert "('context_json', \"TEXT DEFAULT '{}'\")" in src
    assert "'usage_events': [" in src


def test_explicit_material_selection_records_recovery_event():
    src = (ROOT / 'handlers' / 'ai_tutor.py').read_text(encoding='utf-8')
    start = src.index("async def study_content")
    end = src.index("\n\n@router.message(AITutorStates.source", start)
    block = src[start:end]
    assert "await record_event(c.from_user.id, 'open_file'" in block
    assert 'تم اختيار المادة الحالية' in block
