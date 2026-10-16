from __future__ import annotations
import inspect
from sqlalchemy import create_engine, inspect as sa_inspect


def test_clean_json_array_and_fenced_prose():
    from services.ai_parsing import clean_json
    raw = 'مقدمة\n```json\n[{"id":1,"question":"Q","answer":"A"}]\n```\nخاتمة'
    assert clean_json(raw) == [{'id': 1, 'question': 'Q', 'answer': 'A'}]


def test_clean_ai_text_math_artifacts():
    from utils.text import clean_ai_text
    assert clean_ai_text('$$E = h\\times f$$') == 'E = h × f'
    assert clean_ai_text('$$n = \\frac{c}{v}$$') == 'n = c ÷ v'
    assert clean_ai_text('```text\n**عنوان**\n```') == 'عنوان'


def test_telegram_identifier_variants():
    from sources.telegram import normalize_telegram_identifier
    assert normalize_telegram_identifier('@example')[0] == 'example'
    assert normalize_telegram_identifier('example')[0] == 'example'
    assert normalize_telegram_identifier('@channel123')[0] == 'channel123'
    assert normalize_telegram_identifier('@my_channel_2026')[0] == 'my_channel_2026'
    assert normalize_telegram_identifier('https://t.me/example')[0] == 'example'
    assert normalize_telegram_identifier('-1001234567890')[0] == -1001234567890


def test_schema_contains_non_destructive_new_tables_and_columns():
    import models  # noqa: F401
    from database import Base
    engine = create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine)
    names = set(sa_inspect(engine).get_table_names())
    for name in {'school_contents','school_placements','university_resources','university_projects','admin_users','usage_events','study_sessions'}:
        assert name in names
    search_cols = {x['name'] for x in sa_inspect(engine).get_columns('searches')}
    school_cols = {x['name'] for x in sa_inspect(engine).get_columns('school_contents')}
    assert 'section' in search_cols
    assert {'text_content','file_id','mime_type','storage_path','updated_at'} <= school_cols


def test_search_engine_has_local_school_search_and_invalidation():
    from services.search_engine import SearchEngine
    assert hasattr(SearchEngine, '_local_school')
    assert hasattr(SearchEngine, 'invalidate')
    src = inspect.getsource(SearchEngine._local_school)
    assert 'SchoolContent' in src and 'SchoolPlacement' in src


def test_no_fixed_question_or_source_max_constants():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    ai=(root/'handlers'/'ai_tutor.py').read_text(encoding='utf-8')
    assert 'MAX_QUESTIONS' not in ai
    assert 'async def generate_quiz' in ai
    assert 'return await generate_questions' in ai
    assert 'batch_count = min(AI_QUIZ_BATCH_SIZE' in ai
    university=(root/'handlers'/'university.py').read_text(encoding='utf-8')
    assert 'limit=100' not in university


def test_placement_delete_does_not_delete_content():
    from database import Base
    from models import SchoolContent, SchoolPlacement
    from sqlalchemy.orm import Session
    engine = create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        item=SchoolContent(title='رياضيات جزء أول',content_type='📚 كتب',shared=True)
        s.add(item); s.flush()
        a=SchoolPlacement(content_id=item.id,grade='السادس العلمي',branch='علمي',subject='رياضيات')
        b=SchoolPlacement(content_id=item.id,grade='السادس الأدبي',branch='أدبي',subject='رياضيات')
        s.add_all([a,b]); s.commit()
        s.delete(a); s.commit()
        assert s.get(SchoolContent,item.id) is not None
        assert s.query(SchoolPlacement).filter_by(content_id=item.id).count() == 1


def test_usage_events_have_no_user_fk_cascade():
    from database import Base
    from models import User, UsageEvent
    from sqlalchemy.orm import Session
    engine=create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        u=User(telegram_id=123456789,full_name='x')
        s.add(u); s.flush()
        s.add(UsageEvent(user_id=u.id,telegram_id=u.telegram_id,event_type='search'))
        s.commit()
        s.delete(u); s.commit()
        assert s.query(UsageEvent).count() == 1


def test_local_school_search_uses_owner_content(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import services.search_engine as mod

    item = SimpleNamespace(id=1, title='كتاب الجزء الأول', content_type='📚 كتب', teacher='مدرس',
                           stage='', grade='', branch='', department='', subject='',
                           text_content='شرح الدرس', url='', file_id='FILE-1')
    placement = SimpleNamespace(content_id=1, stage='', grade='السادس العلمي', branch='علمي', department='', subject='رياضيات')

    class Result:
        def all(self): return [(item, placement)]
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, stmt): return Result()

    monkeypatch.setattr(mod, 'async_session_maker', lambda: Session())
    async def run():
        engine = mod.SearchEngine()
        profile = SimpleNamespace(path='school', stage='', grade='السادس العلمي', branch='علمي', department='')
        rows = await engine.search('رياضيات', limit=10, section='school', profile=profile)
        assert len(rows) == 1
        assert rows[0]['title'] == 'كتاب الجزء الأول'
        assert rows[0]['metadata']['school_content_id'] == 1
    asyncio.run(run())


def test_chunk_text_respects_telegram_safe_headroom():
    from utils.helpers import chunk_text
    parts=chunk_text(('سؤال طويل جدا ' * 1000).strip(), 3500)
    assert len(parts) > 1
    assert all(len(x) <= 3500 for x in parts)
    assert ''.join(parts).replace(' ', '') == ('سؤالطويلجدا' * 1000)
