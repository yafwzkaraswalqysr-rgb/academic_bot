from __future__ import annotations
import asyncio
import json
import re
import time
from sqlalchemy import select
from database import async_session_maker
from models import SchoolContent, SchoolPlacement, UniversityResource, UniversityProject
from sources.registry import static_sources, search_dynamic
from services.ai_service import ai_service
from utils.text import normalize_arabic, normalize_query, is_arabic
from config import SEARCH_CONCURRENCY, CACHE_TTL_SECONDS

CODE_RE = re.compile(r'(?i)(c\+\+|c#|f#|\.net|java|python|javascript|typescript|rust|golang|go|php|swift|kotlin|matlab|sql|html|css)')


def tokens(text):
    return [x for x in re.findall(r'[a-z0-9+#.\-]+|[\u0600-\u06ff]+', normalize_arabic(text)) if len(x) > 1 or x in {'c++', 'c#'}]


def relevance(q, title, snippet=''):
    nq, nt = normalize_arabic(q), normalize_arabic(title)
    qt, tt = tokens(q), set(tokens(title))
    sn = normalize_arabic(snippet)
    score = 0
    if nq == nt: score += 300
    if nq and nq in nt: score += 160
    overlap = sum(t in tt or t in sn for t in qt)
    score += overlap * 45 + int(120 * overlap / max(1, len(qt)))
    if is_arabic(q) and is_arabic(title): score += 10
    codes = [x.lower() for x in CODE_RE.findall(q)]
    if codes:
        hit = any(c in nt.lower() for c in codes)
        score += 160 if hit else -220
    return score


def _profile_value(profile, key):
    if profile is None:
        return ''
    if isinstance(profile, dict):
        return str(profile.get(key) or '').strip()
    return str(getattr(profile, key, '') or '').strip()


def _norm(v):
    v = normalize_arabic(v)
    v = re.sub(r'^(مهني|خارجي)\s*/\s*', '', v).strip()
    return v


def _school_profile_match(profile, placement):
    if not profile:
        return True
    values = {
        'stage': _norm(_profile_value(profile, 'stage')),
        'grade': _norm(_profile_value(profile, 'grade')),
        'branch': _norm(_profile_value(profile, 'branch')),
        'department': _norm(_profile_value(profile, 'department')),
    }
    for key in ('stage', 'grade', 'branch', 'department'):
        pv = values[key]
        cv = _norm(getattr(placement, key, ''))
        if cv and pv and cv not in {pv, 'عام'}:
            # vocational grade/stage may be stored in either column for legacy rows.
            if key == 'grade' and pv in {'المرحله الاولي','المرحله الثانيه','المرحله الثالثه'} and cv == 'السادس المهني':
                continue
            return False
    return True


class SearchEngine:
    def __init__(self):
        self._cache: dict[tuple, tuple[float, list[dict]]] = {}
        self._cache_version = 0

    def invalidate(self):
        self._cache.clear()
        self._cache_version += 1

    async def _local_school(self, q, profile, limit):
        async with async_session_maker() as s:
            rows = (await s.execute(
                select(SchoolContent, SchoolPlacement)
                .join(SchoolPlacement, SchoolPlacement.content_id == SchoolContent.id)
                .order_by(SchoolContent.id.desc(), SchoolPlacement.id.desc())
            )).all()
        out, seen = [], set()
        nq = normalize_arabic(q)
        for item, placement in rows:
            if item.id in seen or not _school_profile_match(profile, placement):
                continue
            haystack = ' '.join([
                item.title or '', item.content_type or '', item.subject or '', item.grade or '', item.branch or '', item.department or '',
                placement.stage or '', placement.grade or '', placement.branch or '', placement.department or '', placement.subject or '',
                item.text_content or '',
            ])
            n_hay = normalize_arabic(haystack)
            if nq not in n_hay and not any(tok in n_hay for tok in tokens(q)):
                continue
            snippet = (item.text_content or '').strip().replace('\n', ' ')
            if snippet:
                pos = normalize_arabic(snippet).find(nq)
                if pos >= 0:
                    snippet = snippet[max(0, pos-120):pos+700]
                else:
                    snippet = snippet[:700]
            score = relevance(q, item.title, snippet) + 80
            out.append({
                'title': item.title,
                'url': item.url if item.url and not str(item.url).startswith('tgfile:') else '',
                'author': item.teacher or '',
                'year': '',
                'snippet': snippet,
                'source_name': f'مدرستي • {item.content_type}',
                'source_type': 'school',
                'file_url': item.url or '',
                'is_downloadable': bool(item.file_id),
                'tg_chat': None,
                'tg_message_id': None,
                'metadata': {'school_content_id': item.id, 'content_type': item.content_type},
                'rank_score': score,
            })
            seen.add(item.id)
        out.sort(key=lambda x: x['rank_score'], reverse=True)
        return out if not limit or limit <= 0 else out[:limit]

    async def _local_university(self, q, major=None, limit=0):
        async with async_session_maker() as s:
            resources = (await s.execute(select(UniversityResource).where(UniversityResource.enabled.is_(True)))).scalars().all()
            projects = (await s.execute(select(UniversityProject).where(UniversityProject.enabled.is_(True)))).scalars().all()
        out = []
        for x in resources:
            if major and x.major and normalize_arabic(major) not in normalize_arabic(x.major):
                continue
            hay = ' '.join([x.title or '', x.author or '', x.description or '', x.major or '', x.resource_type or ''])
            if normalize_arabic(q) not in normalize_arabic(hay) and not any(t in normalize_arabic(hay) for t in tokens(q)):
                continue
            out.append({'title': x.title, 'url': x.url, 'author': x.author or '', 'year': x.year,
                        'snippet': x.description or '', 'source_name': 'المصادر الجامعية • إدارة المالك',
                        'source_type': 'university', 'metadata': {'university_resource_id': x.id, 'resource_type': x.resource_type},
                        'rank_score': relevance(q, x.title, x.description or '') + 70})
        for x in projects:
            if major and x.major and normalize_arabic(major) not in normalize_arabic(x.major):
                continue
            hay = ' '.join([x.title or '', x.description or '', x.major or ''])
            if normalize_arabic(q) not in normalize_arabic(hay) and not any(t in normalize_arabic(hay) for t in tokens(q)):
                continue
            out.append({'title': x.title, 'url': x.url or '', 'author': '', 'year': x.year,
                        'snippet': x.description or '', 'source_name': 'مشاريع التخرج • إدارة المالك',
                        'source_type': 'university_project', 'metadata': {'university_project_id': x.id},
                        'rank_score': relevance(q, x.title, x.description or '') + 65})
        out.sort(key=lambda x: x['rank_score'], reverse=True)
        return out if not limit or limit <= 0 else out[:limit]

    async def search(self, q, scope=None, date_range=None, year=None, limit=10, mode='fast', major=None, level=None, *, section=None, profile=None):
        q = normalize_query(q)
        section = section or ('school' if profile and _profile_value(profile, 'path') == 'school' else 'university')
        key = (self._cache_version, section, q, scope, date_range, year, int(limit or 0), mode, major, level,
               tuple(sorted((k, _profile_value(profile, k)) for k in ('path','stage','grade','branch','department'))))
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            return [dict(x) for x in cached[1]]

        if section == 'school':
            result = await self._local_school(q, profile, limit)
            self._cache[key] = (time.monotonic(), result)
            return [dict(x) for x in result]

        corrected = await ai_service.correct_query(q)
        corrected = normalize_query(corrected) or q
        queries = [q]
        if corrected not in queries:
            queries.append(corrected)
        if mode == 'deep':
            queries = await ai_service.expand_query(corrected, major, level)

        groups = [self._local_university(corrected, major=major, limit=limit)]
        sem = asyncio.Semaphore(SEARCH_CONCURRENCY)
        async def one(src, qq):
            async with sem:
                try:
                    return await src.search(qq, scope, date_range, year, limit)
                except Exception:
                    return []
        for src in static_sources():
            for qq in queries:
                groups.append(one(src, qq))
        try:
            dynamic = await search_dynamic(corrected, scope, year, limit, mode)
        except Exception:
            dynamic = []
        groups.append(asyncio.sleep(0, result=dynamic))
        fetched = await asyncio.gather(*groups)
        results = [x for group in fetched if isinstance(group, list) for x in group]

        seen, unique = set(), []
        for r in results:
            title = str(r.get('title') or '').strip()
            url = str(r.get('url') or '').strip()
            meta = r.get('metadata') or {}
            key2 = (str(r.get('source_type') or ''), str(meta.get('school_content_id') or meta.get('university_resource_id') or meta.get('university_project_id') or ''),
                    re.sub(r'\W+', '', normalize_arabic(title))[:240] or url)
            if key2 in seen or not title:
                continue
            seen.add(key2)
            r['rank_score'] = relevance(q, title, r.get('snippet', ''))
            if r['rank_score'] >= 20:
                unique.append(r)
        unique.sort(key=lambda x: x.get('rank_score', 0), reverse=True)
        result = unique if not limit or limit <= 0 else unique[:limit]
        self._cache[key] = (time.monotonic(), result)
        return [dict(x) for x in result]

search_engine = SearchEngine()
