from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_current_material_never_uses_usage_event_as_source_of_truth():
    src = (ROOT / 'services' / 'study_session.py').read_text(encoding='utf-8')
    module = __import__('ast').parse(src)
    fn = next(n for n in module.body if isinstance(n, __import__('ast').AsyncFunctionDef) and n.name == 'resolve_current_material')
    names = {n.id for n in __import__('ast').walk(fn) if isinstance(n, __import__('ast').Name)}
    calls = {n.func.id for n in __import__('ast').walk(fn) if isinstance(n, __import__('ast').Call) and isinstance(n.func, __import__('ast').Name)}
    assert 'UsageEvent' not in names
    assert 'get_session' in calls
    assert '_latest_opened_book' not in src


def test_ai_page_range_flow_exists_and_uses_selected_text():
    src = (ROOT / 'handlers' / 'ai_tutor.py').read_text(encoding='utf-8')
    assert "page_range = State()" in src
    assert "callback_data='ai_pages:all'" in src
    assert "callback_data='ai_pages:custom'" in src
    assert 'parse_page_range' in src
    assert '_selected_material_text(material, data)' in src
    assert 'سيتم استخدام الصفحات التي حددتها لهذه الخدمة.' in src


def test_pdf_extraction_keeps_page_boundaries():
    src = (ROOT / 'services' / 'file_service.py').read_text(encoding='utf-8')
    assert '--- الصفحة {index} ---' in src
