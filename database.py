from __future__ import annotations
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import inspect, text
from config import DATABASE_URL

class Base(DeclarativeBase):
    pass

engine = None
_session_factory = None

def _ensure_engine():
    global engine
    if engine is None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
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
                return True

        migrations = {
            'searches': [('section', "VARCHAR(30) DEFAULT 'university'")],
            'school_contents': [
                ('text_content', 'TEXT'), ('photo_id', 'TEXT'), ('video_id', 'TEXT'),
                ('file_id', 'TEXT'), ('mime_type', 'VARCHAR(100)'), ('storage_path', 'TEXT'),
                ('updated_at', 'DATETIME'),
            ],
        }
        for table, columns in migrations.items():
            for name, definition in columns:
                if await has_column(table, name):
                    continue
                try:
                    if conn.dialect.name == 'postgresql':
                        pg_definition = 'TIMESTAMP WITH TIME ZONE' if definition == 'DATETIME' else definition
                        await conn.execute(text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {pg_definition}'))
                    else:
                        await conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))
                except Exception:
                    # A column may have been created concurrently or may not be
                    # present in a very old legacy schema. create_all above covers
                    # fresh installations; we never delete/recreate user data.
                    pass

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
            pass
        try:
            await conn.execute(text(
                'CREATE UNIQUE INDEX IF NOT EXISTS uq_school_placement '
                'ON school_placements (content_id, stage, grade, branch, department, subject)'
            ))
        except Exception:
            pass

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
                pass
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
                    pass


async def close_db():
    global engine, _session_factory
    if engine is not None:
        await engine.dispose()
    engine = None
    _session_factory = None
