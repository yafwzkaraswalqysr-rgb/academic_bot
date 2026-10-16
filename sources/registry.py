from __future__ import annotations
import asyncio
import json
import logging
from sqlalchemy import select
from database import async_session_maker
from models import TelegramSource as TGModel, WebSource as WebModel
from sources.telegram import telegram_source
from sources.api import openalex
from sources.websites import semantic_scholar, crossref, GenericWebSource
from config import SEARCH_TIMEOUT, INTERNAL_RESULT_LIMIT
from services.search_limits import search_provider_limit

log = logging.getLogger(__name__)


def static_sources():
    return [openalex, semantic_scholar, crossref]

async def get_dynamic_sources(mode='fast'):
    async with async_session_maker() as s:
        source_limit = max(1, min(INTERNAL_RESULT_LIMIT, 200))
        tgs = (await s.execute(select(TGModel).where(TGModel.enabled.is_(True)).order_by(TGModel.priority, TGModel.id).limit(source_limit))).scalars().all()
        webs = (await s.execute(select(WebModel).where(WebModel.enabled.is_(True)).order_by(WebModel.priority, WebModel.id).limit(source_limit))).scalars().all()
    return tgs, webs

async def search_dynamic(q, scope=None, year=None, limit=10, mode='fast', purpose=None, query_variants=None):
    tgs, webs = await get_dynamic_sources(mode)
    results = []
    query_variants = [q] if not query_variants else [str(x).strip() for x in query_variants if str(x).strip()]
    seen_queries = set()
    deduped_queries = []
    for item in query_variants:
        key = ''.join(item.casefold().split())
        if key and key not in seen_queries:
            seen_queries.add(key)
            deduped_queries.append(item)
    query_variants = deduped_queries or [q]
    candidates = []
    # The caller may supply specialty/topic-aware variants. Dynamic sources must receive
    # those variants too; otherwise Telegram/Web search would silently ignore the student's specialty.
    for x in sorted(tgs, key=lambda z: z.priority or 0):
        if mode == 'fast' and not x.fast_enabled: continue
        if mode == 'deep' and not x.deep_enabled: continue
        if purpose and (x.source_type or '').strip() not in {purpose, 'both'}: continue
        candidates.append(('telegram', x))
    for x in sorted(webs, key=lambda z: z.priority or 0):
        if mode == 'fast' and not x.fast_enabled: continue
        if mode == 'deep' and not x.deep_enabled: continue
        if purpose and (x.source_type or '').strip() not in {purpose, 'both'}: continue
        candidates.append(('web', x))

    async def one(kind, x):
        async with search_provider_limit.get():
            try:
                collected = []
                for query in query_variants:
                    if kind == 'telegram':
                        rows = await asyncio.wait_for(telegram_source.search_channel(x.username, query, limit=limit), timeout=SEARCH_TIMEOUT)
                    else:
                        try:
                            mapping = json.loads(x.result_mapping_json or '{}')
                            headers = json.loads(x.headers_json or '{}')
                        except Exception:
                            mapping, headers = {}, {}
                        rows = await asyncio.wait_for(
                            GenericWebSource(x.name, x.search_url, mapping, headers).search(query, scope, None, year, limit),
                            timeout=SEARCH_TIMEOUT,
                        )
                    collected.extend(rows or [])
                return collected
            except asyncio.TimeoutError:
                log.warning('Source timed out: %s', getattr(x, 'name', kind))
                return []
            except Exception:
                log.exception('Dynamic source failed: %s', getattr(x, 'name', kind))
                return []

    if candidates:
        groups = await asyncio.gather(*(one(kind, x) for kind, x in candidates), return_exceptions=False)
        for rr in groups:
            results.extend(rr or [])
    return results
