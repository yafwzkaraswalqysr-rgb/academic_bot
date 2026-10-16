from __future__ import annotations
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import inspect, text, event
import logging
from config import DATABASE_URL

log = logging.getLogger(__name__)

class Base(DeclarativeBase):
    pass

engine = None
_session_factory = None

def _ensure_engine():
    global engine
    if engine is None:
        kwargs = {'pool_pre_ping': True}
        if DATABASE_URL.startswith('sqlite'):
            kwargs['connect_args'] = {'timeout': 30}
        engine = create_async_engine(DATABASE_URL, **kwargs)
        if DATABASE_URL.startswith('sqlite'):
            @event.listens_for(engine.sync_engine, 'connect')
            def _set_sqlite_pragmas(dbapi_connection, _connection_record):
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute('PRAGMA foreign_keys=ON')
                finally:
                    cursor.close()
    return engine

def _ensure_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(_ensure_engine(), class_=AsyncSession, expire_on_commit=False)
    return _session_factory

def async_session_maker(*args, **kwargs):
    return _ensure_session_factory()(*args, **kwargs)

async def init_db():
    eng = _ensure_engine()
    # Import every model so metadata includes all current and additive tables.
    from models import (User, Search, Result, LibraryItem, TelegramSource, WebSource, ErrorLog,
                        FollowedTopic, StudentProfile, SchoolContent, SchoolPlacement,
                        UniversityResource, UniversityProject, AdminUser, UsageEvent, StudySession)
    async with eng.begin() as conn:
        if DATABASE_URL.startswith("sqlite"):
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.run_sync(Base.metadata.create_all)

        async def has_column(table_name: str, column_name: str) -> bool:
            def _check(sync_conn):
                return column_name in {c['name'] for c in inspect(sync_conn).get_columns(table_name)}
            try:
                return await conn.run_sync(_check)
            except Exception:
                log.exception('Schema inspection failed for %s.%s', table_name, column_name)
                return False

        migrations = {
            'searches': [('section', "VARCHAR(30) DEFAULT 'university'")],
            'school_contents': [
                ('text_content', 'TEXT'), ('photo_id', 'TEXT'), ('video_id', 'TEXT'),
                ('file_id', 'TEXT'), ('mime_type', 'VARCHAR(100)'), ('storage_path', 'TEXT'),
                ('updated_at', 'DATETIME'),
            ],
            # These fields were introduced after the first StudySession release.
            # create_all() cannot alter an already-existing table, so old Railway
            # databases must receive them additively at startup. Nullable ALTERs
            # preserve old rows; normal writes continue to enforce the application
            # contract through save_session().
            'study_sessions': [
                ('section', "VARCHAR(30) DEFAULT 'university'"),
                ('source_title', "VARCHAR(500)"),
                ('source_text', 'TEXT'),
                ('source_content_id', 'INTEGER'),
                ('source_content_type', 'VARCHAR(100)'),
                ('current_mode', 'VARCHAR(50)'),
                ('context_json', "TEXT DEFAULT '{}'"),
                ('updated_at', 'DATETIME'),
                ('created_at', 'DATETIME'),
            ],
            'usage_events': [
                ('user_id', 'INTEGER'), ('telegram_id', 'INTEGER'), ('event_type', 'VARCHAR(80)'),
                ('metadata_json', 'TEXT'), ('created_at', 'DATETIME'),
            ],
        }
        migration_errors = []
        for table, columns in migrations.items():
            for name, definition in columns:
                if await has_column(table, name):
                    continue
                try:
                    async with conn.begin_nested():
                        if conn.dialect.name == 'postgresql':
                            pg_definition = 'TIMESTAMP WITH TIME ZONE' if definition == 'DATETIME' else definition
                            await conn.execute(text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {pg_definition}'))
                        else:
                            await conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))
                except Exception as exc:
                    log.exception('Migration failed for %s.%s', table, name)
                    migration_errors.append(f'{table}.{name}: {exc}')

        if migration_errors:
            raise RuntimeError('Required database schema migration failed: ' + '; '.join(migration_errors))

        # Legacy school rows must become searchable through placements too.
        try:
            await conn.execute(text("""
                INSERT INTO school_placements (content_id, stage, grade, branch, department, subject)
                SELECT sc.id, COALESCE(sc.stage,''), COALESCE(sc.grade,''),
                       COALESCE(sc.branch,''), COALESCE(sc.department,''), COALESCE(sc.subject,'')
                FROM school_contents sc
                WHERE NOT EXISTS (
                    SELECT 1 FROM school_placements sp WHERE sp.content_id = sc.id
                )
            """))
        except Exception:
            log.exception('Could not migrate legacy school rows into placements')
        try:
            await conn.execute(text(
                'CREATE UNIQUE INDEX IF NOT EXISTS uq_school_placement '
                'ON school_placements (content_id, stage, grade, branch, department, subject)'
            ))
        except Exception:
            log.exception('Could not ensure school placement unique index')

        # Preserve legacy academic sources after source-purpose isolation was
        # introduced. Legacy general/empty records are neutral and therefore map
        # to both university search and graduation; book-only records stay out of
        # academic searches instead of being silently broadened.
        for table in ('telegram_sources', 'web_sources'):
            try:
                await conn.execute(text(
                    f"UPDATE {table} SET source_type='both' WHERE source_type IS NULL OR TRIM(source_type) IN ('', 'general')"
                ))
            except Exception:
                log.exception('Could not normalize legacy source purposes in %s', table)
            try:
                await conn.execute(text(
                    f'CREATE INDEX IF NOT EXISTS idx_{table}_enabled_purpose_priority ON {table} (enabled, source_type, priority)'
                ))
            except Exception:
                log.exception('Could not ensure source index on %s', table)

        # Configuration values are bootstrap values, not an authorization source
        # after the persistent admin table has been populated. This prevents a
        # removed admin from being silently re-added after a Railway restart.
        from config import OWNER_ID, ADMIN_IDS
        try:
            admin_count = int((await conn.execute(text('SELECT COUNT(*) FROM admin_users'))).scalar() or 0)
        except Exception:
            admin_count = 0
        if OWNER_ID:
            try:
                if conn.dialect.name == 'postgresql':
                    await conn.execute(text(
                        'INSERT INTO admin_users (telegram_id, is_owner, added_by) VALUES (:uid, TRUE, :uid) '
                        'ON CONFLICT (telegram_id) DO UPDATE SET is_owner=TRUE'
                    ), {'uid': OWNER_ID})
                else:
                    await conn.execute(text(
                        'INSERT OR IGNORE INTO admin_users (telegram_id, is_owner, added_by) VALUES (:uid, 1, :uid)'
                    ), {'uid': OWNER_ID})
            except Exception:
                log.exception('Could not bootstrap owner admin record')
        if admin_count == 0 and OWNER_ID:
            for uid in ADMIN_IDS:
                if uid == OWNER_ID:
                    continue
                try:
                    if conn.dialect.name == 'postgresql':
                        await conn.execute(text(
                            'INSERT INTO admin_users (telegram_id, is_owner, added_by) VALUES (:uid, FALSE, :owner) ON CONFLICT (telegram_id) DO NOTHING'
                        ), {'uid': uid, 'owner': OWNER_ID})
                    else:
                        await conn.execute(text(
                            'INSERT OR IGNORE INTO admin_users (telegram_id, is_owner, added_by) VALUES (:uid, 0, :owner)'
                        ), {'uid': uid, 'owner': OWNER_ID})
                except Exception:
                    log.exception('Could not bootstrap admin record for telegram_id=%s', uid)


async def close_db():
    global engine, _session_factory
    if engine is not None:
        await engine.dispose()
    engine = None
    _session_factory = None
