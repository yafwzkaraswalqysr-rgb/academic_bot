from __future__ import annotations


def test_source_result_limit_choices_do_not_expose_unlimited():
    from config import RESULT_LIMITS, INTERNAL_RESULT_LIMIT
    assert RESULT_LIMITS == [10, 20, 50]
    assert INTERNAL_RESULT_LIMIT >= max(RESULT_LIMITS)


def test_school_grade_normalization_variants():
    from school_catalog import normalize_school_grade
    assert normalize_school_grade("رابع علمي") == "الرابع العلمي"
    assert normalize_school_grade("الرابع علمي") == "الرابع العلمي"
    assert normalize_school_grade("رابع اعدادي علمي") == "الرابع العلمي"
    assert normalize_school_grade("خامس اعدادي ادبي") == "الخامس الأدبي"
    assert normalize_school_grade("السادس الأدبي") == "السادس الأدبي"


def test_long_material_context_covers_later_sections():
    from services.content_retrieval import retrieve_context
    text = "\n".join([f"SECTION-{i} important topic {i}" for i in range(120)])
    context = retrieve_context(text, max_chars=6000, chunk_chars=1200)
    assert "SECTION-0" in context
    assert "SECTION-119" in context
    assert len(context) <= 6000


def test_question_context_retrieves_relevant_late_chunk():
    from services.content_retrieval import retrieve_context
    text = "\n".join([f"section {i} general material" for i in range(80)] + ["quantum superconductivity josephson junction"] * 8)
    context = retrieve_context(text, "ما هو Josephson junction؟", max_chars=5000, chunk_chars=900)
    assert "josephson" in context.lower()


def test_study_session_is_user_scoped():
    from models import StudySession
    cols = StudySession.__table__.c
    assert cols.user_id.unique is True
    assert cols.user_id.foreign_keys


def test_ai_stateful_natural_handler_is_exact_match_filter():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "handlers" / "ai_tutor.py").read_text(encoding="utf-8")
    assert '@router.message(StateFilter(None), F.text.in_(NATURAL_MODE_FILTER_VALUES))' in src
    assert '@router.message(StateFilter(None), F.text)\nasync def ai_natural_command_without_state' not in src


def test_db_source_legacy_migration_is_non_destructive():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "database.py").read_text(encoding="utf-8")
    assert "telegram_sources" in src and "source_type='both'" in src
    assert "web_sources" in src and "source_type='both'" in src
    assert 'DROP TABLE' not in src.upper()
    assert 'DROP DATABASE' not in src.upper()


def test_dynamic_sources_respect_graduation_purpose(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import sources.registry as registry

    tgs = [
        SimpleNamespace(username='uni', name='University', source_type='university_search', fast_enabled=True, deep_enabled=True, priority=1),
        SimpleNamespace(username='grad', name='Graduation', source_type='graduation', fast_enabled=True, deep_enabled=True, priority=2),
        SimpleNamespace(username='both', name='Both', source_type='both', fast_enabled=True, deep_enabled=True, priority=3),
    ]
    webs = [
        SimpleNamespace(name='Web graduation', search_url='https://example.com/?q={query}', result_mapping_json='{}', headers_json='{}', source_type='graduation', fast_enabled=True, deep_enabled=True, priority=1),
        SimpleNamespace(name='Web university', search_url='https://example.com/?q={query}', result_mapping_json='{}', headers_json='{}', source_type='university_search', fast_enabled=True, deep_enabled=True, priority=2),
    ]
    async def fake_sources(mode='fast'):
        return tgs, webs
    monkeypatch.setattr(registry, 'get_dynamic_sources', fake_sources)

    async def fake_tg(identifier, q, limit=0):
        return [{'title': identifier, 'source_name': identifier, 'source_type': 'telegram', 'snippet': q}]
    monkeypatch.setattr(registry.telegram_source, 'search_channel', fake_tg)

    class FakeWeb:
        def __init__(self, name, search_url, mapping=None, headers=None): self.name = name
        async def search(self, q, scope=None, date_range=None, year=None, limit=10):
            return [{'title': self.name, 'source_name': self.name, 'source_type': 'website', 'snippet': q}]
    monkeypatch.setattr(registry, 'GenericWebSource', FakeWeb)

    async def run():
        rows = await registry.search_dynamic('thesis', limit=5, purpose='graduation')
        names = {r['source_name'] for r in rows}
        assert 'grad' in names and 'both' in names
        assert 'uni' not in names
        assert 'Web graduation' in names
        assert 'Web university' not in names
    asyncio.run(run())


def test_callback_indices_are_validated_before_state_lookup():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'handlers' / 'callbacks.py').read_text(encoding='utf-8')
    assert 'if i < 0 or i >= len(SEARCH_SCOPES):' in src
    assert 'if i < 0 or i >= len(DATE_RANGES):' in src


def test_admin_bootstrap_failures_are_logged():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'database.py').read_text(encoding='utf-8')
    assert "log.exception('Could not bootstrap owner admin record')" in src
    assert "log.exception('Could not bootstrap admin record for telegram_id=%s', uid)" in src


def test_telegram_start_disconnects_partial_client_on_failure():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'sources' / 'telegram.py').read_text(encoding='utf-8')
    assert 'await client.disconnect()' in src


def test_external_api_sources_retry_transient_errors():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'sources' / 'websites.py').read_text(encoding='utf-8')
    assert 'for attempt in range(3):' in src
    assert 'r.status == 429 or 500 <= r.status < 600' in src


def test_natural_material_commands_are_normalized():
    from pathlib import Path
    import ast
    src = (Path(__file__).resolve().parents[1] / 'handlers' / 'ai_tutor.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    aliases = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'NATURAL_MODE_ALIASES' for t in node.targets):
            aliases = ast.literal_eval(node.value)
            break
    assert aliases
    from utils.text import normalize_arabic
    normalized = {normalize_arabic(k): v for k, v in aliases.items()}
    assert normalized[normalize_arabic('اسألني')] == 'ai_ask'
    assert normalized[normalize_arabic('لخص المادة')] == 'ai_summary'
    assert normalized[normalize_arabic('أعطني أسئلة')] == 'ai_ask'
    assert normalized[normalize_arabic('أمتحني')] == 'ai_mixed'


def test_user_callbacks_validate_integer_counts_and_search_modes():
    from pathlib import Path
    uni = (Path(__file__).resolve().parents[1] / 'handlers' / 'university.py').read_text(encoding='utf-8')
    cb = (Path(__file__).resolve().parents[1] / 'handlers' / 'callbacks.py').read_text(encoding='utf-8')
    assert "except (TypeError, ValueError):" in uni
    assert "if value <= 0 or value > INTERNAL_RESULT_LIMIT:" in uni
    assert "if value not in {'fast','deep'}:" in cb


def test_graduation_runs_ai_and_source_tasks_independently():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'handlers' / 'graduation.py').read_text(encoding='utf-8')
    assert 'asyncio.create_task(asyncio.wait_for(' in src
    assert 'asyncio.gather(*tasks, return_exceptions=True)' in src


def test_web_admin_shows_source_purpose():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'handlers' / 'admin.py').read_text(encoding='utf-8')
    assert 'purpose_labels' in src
    assert "'graduation':'🎓 مشروع التخرج'" in src


def test_user_text_is_html_escaped_in_html_messages():
    from pathlib import Path
    grad = (Path(__file__).resolve().parents[1] / 'handlers' / 'graduation.py').read_text(encoding='utf-8')
    admin = (Path(__file__).resolve().parents[1] / 'handlers' / 'admin.py').read_text(encoding='utf-8')
    assert 'safe_html_escape(major)' in grad
    assert "safe_html_escape(m.text.strip())" in admin
