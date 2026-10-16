from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def _function_source(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(path.read_text(encoding='utf-8'), node) or ''
    raise AssertionError(f'{name} not found in {path}')


def test_save_session_has_strong_success_semantics():
    src = _function_source(ROOT / 'services' / 'study_session.py', 'save_session')
    assert 'raise RuntimeError' in src
    assert 'async with async_session_maker() as verify_session' in src
    assert 'select(StudySession).where(StudySession.user_id == uid)' in src
    assert 'return None' not in src


def test_file_upload_does_not_claim_success_without_database_persistence():
    src = (ROOT / 'handlers' / 'files.py').read_text(encoding='utf-8')
    assert 'saved = await save_session' in src
    assert 'لم يتم تثبيت المادة الدراسية في قاعدة البيانات.' in src


def test_school_open_repairs_legacy_books_with_missing_extracted_text():
    src = _function_source(ROOT / 'handlers' / 'ai_tutor.py', 'study_content')
    assert 'extract_telegram_document' in src
    assert 'stored.text_content = material_text' in src
    assert 'saved = await _save_material' in src
    assert "'open_file'" in src


def test_open_file_event_is_only_recorded_after_material_persistence():
    src = _function_source(ROOT / 'handlers' / 'ai_tutor.py', 'study_content')
    assert 'saved = await _save_material' in src
    assert "await record_event(c.from_user.id, 'open_file'" in src


def test_natural_exam_command_resolves_durable_material():
    src = (ROOT / 'handlers' / 'ai_tutor.py').read_text(encoding='utf-8')
    assert "'اي نوع من الامتحانات': 'ai_mixed'" in src
    assert 'material = await _current_material(m.from_user.id)' in src
    assert 'return await start_mode(m, state, mode)' in src


def test_persistence_budget_is_larger_than_model_context_budget():
    config = (ROOT / 'config.py').read_text(encoding='utf-8')
    assert "_int('MAX_STUDY_TEXT_CHARS',10_000_000)" in config
