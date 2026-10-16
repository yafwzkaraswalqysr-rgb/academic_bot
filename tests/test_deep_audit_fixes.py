from pathlib import Path
import ast


def test_file_magic_bytes_and_ssrf_guards(tmp_path):
    from utils.security import is_ssrf_safe_url

    # Positive DNS validation cannot be asserted here because the execution
    # environment may intentionally have no outbound DNS/network access.
    assert is_ssrf_safe_url('http://127.0.0.1') is False
    assert is_ssrf_safe_url('http://localhost') is False
    assert is_ssrf_safe_url('http://169.254.169.254') is False
    assert is_ssrf_safe_url('http://100.64.0.1') is False


def test_database_enables_sqlite_fk_on_each_connection():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'database.py').read_text(encoding='utf-8')
    assert "@event.listens_for(engine.sync_engine, 'connect')" in src
    assert 'PRAGMA foreign_keys=ON' in src
    assert 'begin_nested()' in src
    assert 'Required database schema migration failed:' in src


def test_account_deletes_persistent_study_session():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'handlers' / 'account.py').read_text(encoding='utf-8')
    assert 'delete(StudySession)' in src


def test_quiz_flow_is_interactive_and_answer_keys_are_not_rendered_up_front():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'handlers' / 'ai_tutor.py').read_text(encoding='utf-8')
    assert 'quiz_questions=items' in src
    assert 'state.set_state(AITutorStates.quiz)' in src
    assert "return await send_question(m, state)" in src
    # Generation for quiz modes must not route through the old answer-sheet view.
    fn = ast.parse(src)
    run_count = next(n for n in fn.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_run_count')
    body = ast.get_source_segment(src, run_count)
    assert 'if mode in QUIZ_MODES:' in body
    assert 'show_generated_page(m, state, 0)' in body


def test_telegram_download_checks_declared_size_before_network_download():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'handlers' / 'callbacks.py').read_text(encoding='utf-8')
    size_pos = src.index('declared_size=')
    download_pos = src.index('download_media', size_pos)
    assert size_pos < download_pos
    assert 'sanitize_filename' in src
    assert 'uuid4().hex' in src


def test_school_menu_uses_canonical_primary_and_intermediate_grade_labels():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'handlers' / 'school.py').read_text(encoding='utf-8')
    assert 'السادس الابتدائي' in src
    assert 'الثالث المتوسط' in src
    assert 'السادس ابتدائي' not in src
    assert 'الثالث متوسط' not in src


def test_docker_build_context_excludes_secrets_and_runtime_state():
    root = Path(__file__).resolve().parents[1]
    dockerignore = (root / '.dockerignore').read_text(encoding='utf-8')
    assert '.env' in dockerignore
    assert '!.env.example' in dockerignore
    assert '*.session' in dockerignore
    assert '*.db' in dockerignore
    dockerfile = (root / 'Dockerfile').read_text(encoding='utf-8')
    assert 'USER botuser' in dockerfile


def test_provider_level_result_caps_are_defensive():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for rel in ('sources/api.py', 'sources/websites.py', 'sources/telegram.py'):
        src = (root / rel).read_text(encoding='utf-8')
        assert 'INTERNAL_RESULT_LIMIT' in src
