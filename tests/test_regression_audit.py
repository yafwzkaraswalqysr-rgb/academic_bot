from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def test_study_content_imports_models_used_by_callback():
    src = (ROOT / 'handlers' / 'ai_tutor.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    imports = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == 'models':
            imports.update(a.name for a in node.names)
    assert {'User', 'StudentProfile', 'SchoolPlacement'}.issubset(imports)


def test_study_content_validates_callback_id():
    src = (ROOT / 'handlers' / 'ai_tutor.py').read_text(encoding='utf-8')
    assert "except (TypeError, ValueError):" in src
    assert "return await c.answer('المادة غير صالحة.', show_alert=True)" in src


def test_mcq_numeric_answers_are_not_ambiguous():
    src = (ROOT / 'handlers' / 'ai_tutor.py').read_text(encoding='utf-8')
    assert 'numeric == idx + 1' in src
    assert 'numeric == idx or int(a) == idx + 1' not in src


def test_file_section_uses_student_path():
    src = (ROOT / 'handlers' / 'files.py').read_text(encoding='utf-8')
    assert "section = 'school' if profile and profile.path == 'school' else 'university'" in src
    assert "if not profile:\n            section = 'university'" not in src


def test_library_user_and_external_text_is_html_escaped():
    src = (ROOT / 'handlers' / 'library.py').read_text(encoding='utf-8')
    assert 'safe_html_escape(r.title)' in src
    assert 'safe_html_escape(r.url or "بدون رابط")' in src
    assert "parse_mode='HTML'" in src


def test_download_caption_is_html_escaped():
    src = (ROOT / 'handlers' / 'callbacks.py').read_text(encoding='utf-8')
    assert "caption=safe_html_escape(truncate(r.title or file_name,900))" in src


def test_school_menu_no_longer_queries_placements_per_item():
    src = (ROOT / 'handlers' / 'school.py').read_text(encoding='utf-8')
    assert 'matched.append((item, placement))' in src
    assert 'for item, placement in items:' in src
    assert 'for item in items:' not in src


def test_study_session_has_central_size_guard():
    src = (ROOT / 'services' / 'study_session.py').read_text(encoding='utf-8')
    assert 'MAX_STUDY_TEXT_CHARS' in src
    assert 'if len(text) > MAX_STUDY_TEXT_CHARS:' in src


def test_file_and_image_paths_do_not_echo_raw_exception_to_user():
    files = (ROOT / 'handlers' / 'files.py').read_text(encoding='utf-8')
    image = (ROOT / 'handlers' / 'voice_image.py').read_text(encoding='utf-8')
    assert 'user_reason = reason if isinstance(exc, ValueError)' in files
    assert 'user_reason = str(exc) if isinstance(exc, ValueError)' in image


def test_logging_uses_rotating_file_handler():
    src = (ROOT / 'utils' / 'logging.py').read_text(encoding='utf-8')
    assert 'RotatingFileHandler' in src
    assert 'backupCount=3' in src


def test_ai_unavailability_is_not_reported_as_successful_graduation_analysis():
    src = (ROOT / 'handlers' / 'graduation.py').read_text(encoding='utf-8')
    assert "if plan:" in src
    assert 'الذكاء الاصطناعي غير متاح حاليًا؛' in src or 'الذكاء الاصطناعي غير متاح حاليًا، لذلك لم يتم توليد التحليل.' in src


def test_external_search_uses_shared_process_budget():
    engine = (ROOT / 'services' / 'search_engine.py').read_text(encoding='utf-8')
    registry = (ROOT / 'sources' / 'registry.py').read_text(encoding='utf-8')
    assert 'search_provider_limit.get()' in engine
    assert 'search_provider_limit.get()' in registry
    assert 'sem = asyncio.Semaphore(SEARCH_CONCURRENCY)' not in engine
    assert 'sem = asyncio.Semaphore(max(1, SEARCH_CONCURRENCY))' not in registry


def test_search_applies_user_selected_recent_date_range():
    src = (ROOT / 'services' / 'search_engine.py').read_text(encoding='utf-8')
    assert "'آخر سنة': 1" in src
    assert "'آخر 3 سنوات': 3" in src
    assert "'آخر 5 سنوات': 5" in src
    assert 'first_year <= item_year <= current_year' in src
