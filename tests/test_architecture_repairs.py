from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def test_current_material_resolver_is_persistent_and_centralized():
    study = (ROOT / 'services' / 'study_session.py').read_text(encoding='utf-8')
    ai = (ROOT / 'handlers' / 'ai_tutor.py').read_text(encoding='utf-8')
    assert 'async def resolve_current_material(uid, session_id: int | None = None):' in study
    assert 'StudySession' in study
    assert "UsageEvent.event_type == 'open_file'" not in study
    assert '_latest_opened_book' not in study
    assert 'await s.delete(row)' in study
    assert 'from services.study_session import' in ai and 'resolve_current_material' in ai
    assert 'async def ai_stale_state_command_guard' not in ai
    assert '@router.message(F.text)\nasync def ai_stale_state_command_guard' not in ai


def test_deduplicate_results_across_academic_providers():
    from services.search_engine import deduplicate_results

    rows = [
        {
            'title': 'Deep Learning for Medical Image Analysis',
            'url': 'https://doi.org/10.1234/ABC',
            'source_name': 'OpenAlex', 'source_type': 'api',
            'metadata': {'doi': '10.1234/ABC'}, 'rank_score': 120,
        },
        {
            'title': 'Deep Learning for Medical Image Analysis',
            'url': 'https://api.crossref.org/works/abc',
            'source_name': 'CrossRef', 'source_type': 'api',
            'metadata': {'DOI': '10.1234/abc'}, 'rank_score': 110,
        },
        {
            'title': 'Deep Learning for Medical Image Analysis',
            'url': 'https://semanticscholar.org/paper/1',
            'source_name': 'Semantic Scholar', 'source_type': 'api',
            'metadata': {}, 'rank_score': 95,
        },
        {
            'title': 'A Different Deep Learning Study',
            'url': 'https://example.org/other',
            'source_name': 'Telegram', 'source_type': 'telegram',
            'metadata': {}, 'rank_score': 80,
        },
    ]
    out = deduplicate_results(rows)
    assert len(out) == 2
    assert out[0]['source_name'] == 'OpenAlex'


def test_dynamic_sources_receive_specialty_aware_query_variants(monkeypatch):
    import sources.registry as registry

    tgs = [SimpleNamespace(
        username='specialty', name='Specialty TG', source_type='university_search',
        fast_enabled=True, deep_enabled=True, priority=1,
    )]
    webs = [SimpleNamespace(
        name='Specialty Web', search_url='https://example.com/?q={query}',
        result_mapping_json='{}', headers_json='{}', source_type='university_search',
        fast_enabled=True, deep_enabled=True, priority=1,
    )]

    async def fake_sources(mode='fast'):
        return tgs, webs

    monkeypatch.setattr(registry, 'get_dynamic_sources', fake_sources)
    seen_tg = []
    seen_web = []

    async def fake_tg(identifier, q, limit=0):
        seen_tg.append(q)
        return [{'title': q, 'source_name': 'Specialty TG', 'source_type': 'telegram', 'snippet': q}]

    monkeypatch.setattr(registry.telegram_source, 'search_channel', fake_tg)

    class FakeWeb:
        def __init__(self, name, search_url, mapping=None, headers=None):
            self.name = name
        async def search(self, q, scope=None, date_range=None, year=None, limit=10):
            seen_web.append(q)
            return [{'title': q, 'source_name': 'Specialty Web', 'source_type': 'website', 'snippet': q}]

    monkeypatch.setattr(registry, 'GenericWebSource', FakeWeb)

    async def run():
        rows = await registry.search_dynamic(
            'الذكاء الاصطناعي', limit=5, purpose='university_search',
            query_variants=['الذكاء الاصطناعي هندسة حاسبات', 'Artificial Intelligence Computer Engineering'],
        )
        assert rows

    asyncio.run(run())
    assert 'الذكاء الاصطناعي هندسة حاسبات' in seen_tg
    assert 'Artificial Intelligence Computer Engineering' in seen_tg
    assert 'الذكاء الاصطناعي هندسة حاسبات' in seen_web
    assert 'Artificial Intelligence Computer Engineering' in seen_web


def test_natural_graduation_project_trigger_exists():
    src = (ROOT / 'handlers' / 'graduation.py').read_text(encoding='utf-8')
    assert 'GRADUATION_TRIGGER_TEXTS' in src
    assert 'أريد مشروع تخرج' in src
    assert 'أريد مشروع التخرج' in src
    assert "F.text.in_(GRADUATION_TRIGGER_TEXTS)" in src


def test_no_duplicate_help_or_redundant_start_handlers():
    account = (ROOT / "handlers" / "account.py").read_text(encoding="utf-8")
    start = (ROOT / "handlers" / "start.py").read_text(encoding="utf-8")
    assert "F.text == 'ℹ️ المساعدة'" not in account
    assert 'Command("start")' not in start
    assert 'CommandStart()' in start


def test_deduplicate_results_can_merge_near_identical_titles_without_doi():
    from services.search_engine import deduplicate_results
    rows = [
        {'title': 'Machine Learning for Medical Diagnosis', 'url': 'https://example.org/a', 'source_name': 'OpenAlex', 'source_type': 'api', 'metadata': {}, 'rank_score': 100},
        {'title': 'Machine Learning for Medical Diagnosis.', 'url': 'https://example.org/b', 'source_name': 'CrossRef', 'source_type': 'api', 'metadata': {}, 'rank_score': 90},
    ]
    out = deduplicate_results(rows)
    assert len(out) == 1
    assert out[0]['source_name'] == 'OpenAlex'


def test_resolve_current_material_reads_persistent_session(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from services import study_session

    row = SimpleNamespace(
        source_title='Physics Book', source_text='Newton laws and motion', section='university',
        source_content_id=None, source_content_type='pdf', current_mode=None, context_json='{}',
        updated_at=None,
    )

    async def fake_get_session(uid):
        assert uid == 123
        return row

    monkeypatch.setattr(study_session, 'get_session', fake_get_session)
    result = asyncio.run(study_session.resolve_current_material(123))
    assert result['title'] == 'Physics Book'
    assert result['text'] == 'Newton laws and motion'
    assert result['section'] == 'university'
