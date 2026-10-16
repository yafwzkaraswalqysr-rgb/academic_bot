from pathlib import Path


def test_search_asks_for_major_before_university_search():
    src = Path('handlers/search.py').read_text(encoding='utf-8')
    assert 'SearchStates.major' in src
    assert 'شنو اختصاصك؟' in src
    assert "d.get('major') or u.major" in src


def test_search_engine_builds_major_scoped_queries():
    src = Path('services/search_engine.py').read_text(encoding='utf-8')
    assert 'major_text' in src
    assert 'scoped.append' in src


def test_current_material_does_not_resurrect_latest_opened_book():
    src = Path('services/study_session.py').read_text(encoding='utf-8')
    assert '_latest_opened_book' not in src
    module = __import__('ast').parse(src)
    fn = next(n for n in module.body if isinstance(n, __import__('ast').AsyncFunctionDef) and n.name == 'resolve_current_material')
    calls = {n.func.id for n in __import__('ast').walk(fn) if isinstance(n, __import__('ast').Call) and isinstance(n.func, __import__('ast').Name)}
    assert 'get_session' in calls
    assert not any(isinstance(n, __import__('ast').Name) and n.id == 'UsageEvent' for n in __import__('ast').walk(fn))


def test_exam_supports_explicit_page_range():
    src = Path('handlers/ai_tutor.py').read_text(encoding='utf-8')
    assert 'ai_pages:all' in src
    assert 'ai_pages:custom' in src
    assert 'parse_page_range' in src
    assert 'ai_stale_state_command_guard' not in src
    study = Path('services/study_session.py').read_text(encoding='utf-8')
    assert 'resolve_current_material' in study


def test_explicit_end_has_a_durable_boundary():
    src = Path('services/study_session.py').read_text(encoding='utf-8')
    assert "'study_session_ended'" in src
    assert 'await s.delete(row)' in src
    assert 'await s.commit()' in src


def test_clear_session_records_end_tombstone():
    src = Path('services/study_session.py').read_text(encoding='utf-8')
    block = src[src.index('async def clear_session'):src.index('async def _load_session_context')]
    assert "record_event(telegram_id, 'study_session_ended'" in block
