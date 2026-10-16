from __future__ import annotations
import json
from sqlalchemy import select
from database import async_session_maker
from models import TelegramSource as TGModel, WebSource as WebModel
from sources.telegram import telegram_source
from sources.api import openalex
from sources.websites import semantic_scholar, crossref, GenericWebSource


def static_sources():
    return [openalex, semantic_scholar, crossref]

async def get_dynamic_sources(mode='fast'):
    async with async_session_maker() as s:
        tgs = (await s.execute(select(TGModel).where(TGModel.enabled.is_(True)))).scalars().all()
        webs = (await s.execute(select(WebModel).where(WebModel.enabled.is_(True)))).scalars().all()
    return tgs, webs

async def search_dynamic(q, scope=None, year=None, limit=10, mode='fast'):
    tgs, webs = await get_dynamic_sources(mode)
    results = []
    for x in sorted(tgs, key=lambda z: z.priority or 0):
        if mode == 'fast' and not x.fast_enabled: continue
        if mode == 'deep' and not x.deep_enabled: continue
        try:
            rr = await telegram_source.search_channel(x.username, q, limit=limit)
            results.extend(rr)
        except Exception:
            continue
    for x in sorted(webs, key=lambda z: z.priority or 0):
        if mode == 'fast' and not x.fast_enabled: continue
        if mode == 'deep' and not x.deep_enabled: continue
        try:
            mapping = json.loads(x.result_mapping_json or '{}')
            headers = json.loads(x.headers_json or '{}')
        except Exception:
            mapping, headers = {}, {}
        try:
            rr = await GenericWebSource(x.name, x.search_url, mapping, headers).search(q, scope, None, year, limit)
            results.extend(rr)
        except Exception:
            continue
    return results
