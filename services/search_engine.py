from __future__ import annotations
import asyncio
import json
import re
import time
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from sqlalchemy import select, or_
from database import async_session_maker
from models import SchoolContent, SchoolPlacement, UniversityResource, UniversityProject
from sources.registry import static_sources, search_dynamic
from services.ai_service import ai_service
from utils.text import normalize_arabic, normalize_query, is_arabic
from school_catalog import normalize_school_grade
from config import CACHE_TTL_SECONDS, CACHE_MAX_ENTRIES, INTERNAL_RESULT_LIMIT, SEARCH_TIMEOUT
from services.search_limits import search_provider_limit

CODE_RE = re.compile(r'(?i)(c\+\+|c#|f#|\.net|java|python|javascript|typescript|rust|golang|go|php|swift|kotlin|matlab|sql|html|css)')

# Deterministic bilingual concepts used before the optional AI query expansion.
# A concept only helps when the result itself contains the concept phrase; a
# single generic word such as "data" is never sufficient by itself.
ACADEMIC_CONCEPTS = {
    'data_science': {
        'labels': ('علوم البيانات', 'علم البيانات', 'data science', 'data scientist',
                   'data analytics', 'تحليل البيانات', 'data analysis', 'data mining',
                   'تنقيب البيانات', 'machine learning', 'تعلم الالة', 'تعلم الآلة',
                   'statistics for data science', 'احصاء علوم البيانات'),
        'expansions': ('Data Science', 'علوم البيانات', 'Data Analysis', 'تحليل البيانات',
                       'Machine Learning', 'تعلم الآلة', 'Data Mining', 'تنقيب البيانات'),
    },
    'machine_learning': {
        'labels': ('machine learning', 'تعلم الالة', 'تعلم الآلة', 'التعلم الالي', 'التعلم الآلي'),
        'expansions': ('Machine Learning', 'تعلم الآلة', 'deep learning', 'التعلم العميق'),
    },
    'artificial_intelligence': {
        'labels': ('artificial intelligence', 'ai', 'الذكاء الاصطناعي', 'ذكاء اصطناعي'),
        'expansions': ('Artificial Intelligence', 'الذكاء الاصطناعي', 'Machine Learning', 'تعلم الآلة'),
    },
}

def tokens(text):
    return [x for x in re.findall(r'[a-z0-9+#.\-]+|[\u0600-\u06ff]+', normalize_arabic(text)) if len(x) > 1 or x in {'c++', 'c#'}]


def _matched_concepts(text):
    norm = normalize_arabic(text)
    return [name for name, data in ACADEMIC_CONCEPTS.items()
            if any(normalize_arabic(label) in norm for label in data['labels'])]


def expand_query_terms(q):
    """Return safe bilingual query variants for known academic concepts."""
    variants = [str(q or '').strip()]
    matched = _matched_concepts(q)
    for name in matched:
        for item in ACADEMIC_CONCEPTS[name]['expansions']:
            if item and item not in variants:
                variants.append(item)
    # Avoid exploding provider traffic while keeping both Arabic and English
    # forms plus one or two close academic terms.
    out = []
    seen = set()
    for item in variants:
        key = normalize_arabic(item)
        if key and key not in seen:
            seen.add(key); out.append(item)
    return out[:5]


def _source_quality(url='', source_type='', source_name=''):
    value = normalize_arabic(f'{url} {source_type} {source_name}')
    score = 0
    if '.edu' in value or '.ac.' in value or '.edu.' in value:
        score += 35
    if any(x in value for x in ('openalex', 'semantic scholar', 'crossref', 'doi.org')):
        score += 30
    if source_type == 'api':
        score += 12
    elif source_type in {'university', 'university_project'}:
        score += 20
    return score


def _canonical_doi(value):
    value = str(value or '').strip().lower()
    value = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', value)
    value = re.sub(r'^doi:\s*', '', value)
    return value.strip().rstrip(' .;,')


def _canonical_url(value):
    raw = str(value or '').strip()
    if not raw:
        return ''
    try:
        parts = urlsplit(raw)
        if parts.scheme not in {'http', 'https'} or not parts.netloc:
            return raw.lower().rstrip('/')
        host = parts.netloc.lower().removeprefix('www.')
        ignored = {'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'ref', 'source'}
        query = [(k.lower(), v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in ignored]
        query.sort()
        path = re.sub(r'/+', '/', parts.path or '/').rstrip('/') or '/'
        return urlunsplit((parts.scheme.lower(), host, path, urlencode(query), ''))
    except Exception:
        return raw.lower().rstrip('/')


def _canonical_title(value):
    text = normalize_arabic(str(value or '')).lower()
    text = re.sub(r'[^a-z0-9\u0600-\u06ff]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _title_tokens(value):
    return set(re.findall(r'[a-z0-9]+|[\u0600-\u06ff]+', _canonical_title(value)))


def _same_work(a, b):
    meta_a, meta_b = a.get('metadata') or {}, b.get('metadata') or {}
    doi_a = _canonical_doi(meta_a.get('doi') or meta_a.get('DOI'))
    doi_b = _canonical_doi(meta_b.get('doi') or meta_b.get('DOI'))
    if doi_a and doi_b and doi_a == doi_b:
        return True
    url_a, url_b = _canonical_url(a.get('url') or ''), _canonical_url(b.get('url') or '')
    if url_a and url_b and url_a == url_b:
        return True
    ta, tb = _canonical_title(a.get('title')), _canonical_title(b.get('title'))
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    ratio = SequenceMatcher(None, ta, tb).ratio()
    toks_a, toks_b = _title_tokens(ta), _title_tokens(tb)
    overlap = len(toks_a & toks_b) / max(1, len(toks_a | toks_b))
    return ratio >= 0.94 and overlap >= 0.86


def deduplicate_results(results):
    # Merge the same work reported by multiple providers without hiding distinct works.
    unique = []
    for item in results:
        if not item or not str(item.get('title') or '').strip():
            continue
        match_index = next((i for i, existing in enumerate(unique) if _same_work(existing, item)), None)
        if match_index is None:
            unique.append(item)
            continue
        existing = unique[match_index]
        existing_score = int(existing.get('rank_score', 0) or 0)
        incoming_score = int(item.get('rank_score', 0) or 0)
        if incoming_score > existing_score:
            winner, other = item, existing
        else:
            winner, other = existing, item
        merged = dict(winner)
        for key in ('url', 'file_url', 'author', 'year', 'snippet', 'source_name', 'source_type'):
            if not merged.get(key) and other.get(key):
                merged[key] = other.get(key)
        meta = dict(other.get('metadata') or {})
        meta.update(winner.get('metadata') or {})
        if not meta.get('doi'):
            other_meta = other.get('metadata') or {}
            if other_meta.get('doi'):
                meta['doi'] = other_meta['doi']
        merged['metadata'] = meta
        unique[match_index] = merged
    return unique


def relevance(q, title, snippet='', *, url='', source_type='', source_name='', metadata=None):
    nq, nt = normalize_arabic(q), normalize_arabic(title)
    qt, tt = tokens(q), set(tokens(title))
    sn = normalize_arabic(snippet)
    combined = ' '.join(x for x in (nt, sn) if x)
    score = 0
    if nq == nt: score += 320
    if nq and nq in nt: score += 220
    if nq and nq in sn: score += 90

    title_hits = sum(t in tt for t in qt)
    body_hits = sum(t in sn for t in qt)
    coverage = title_hits + body_hits
    if qt:
        score += title_hits * 75 + body_hits * 28
        score += int(170 * min(1.0, coverage / len(qt)))

    concept_names = _matched_concepts(q)
    concept_hits = 0
    for name in concept_names:
        labels = ACADEMIC_CONCEPTS[name]['labels']
        if any(normalize_arabic(label) in combined for label in labels):
            concept_hits += 1
    if concept_names:
        score += concept_hits * 115
        # For a recognized concept, generic single-token matches are not enough.
        if not concept_hits:
            score -= 160

    if len(qt) >= 2 and coverage == 1 and not concept_hits:
        score -= 85
    if coverage == 0 and not concept_hits:
        score -= 220

    if is_arabic(q) and is_arabic(title): score += 10
    codes = [x.lower() for x in CODE_RE.findall(q)]
    if codes:
        hit = any(c in nt.lower() or c in sn.lower() for c in codes)
        score += 170 if hit else -220

    score += _source_quality(url, source_type, source_name)
    if metadata and metadata.get('doi'):
        score += 8
    return score


def _profile_value(profile, key):
    if profile is None:
        return ''
    if isinstance(profile, dict):
        return str(profile.get(key) or '').strip()
    return str(getattr(profile, key, '') or '').strip()


def _norm(v):
    v = normalize_arabic(v)
    v = re.sub(r'\s+', ' ', v).strip()
    if v.startswith(('مهني /', 'خارجي /')):
        v = v.split('/', 1)[1].strip()
    return normalize_school_grade(v)


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

    def _prune_cache(self):
        now = time.monotonic()
        expired = [k for k, (created, _) in self._cache.items() if now - created >= CACHE_TTL_SECONDS]
        for key in expired:
            self._cache.pop(key, None)
        overflow = len(self._cache) - CACHE_MAX_ENTRIES
        if overflow > 0:
            oldest = sorted(self._cache.items(), key=lambda item: item[1][0])[:overflow]
            for key, _ in oldest:
                self._cache.pop(key, None)

    async def _local_school(self, q, profile, limit):
        nq = normalize_arabic(q)
        terms = tokens(q)[:8]
        candidate_limit = min(max((limit or INTERNAL_RESULT_LIMIT) * 20, 500), 5000)
        search_columns = [
            SchoolContent.title, SchoolContent.content_type, SchoolContent.subject,
            SchoolContent.grade, SchoolContent.branch, SchoolContent.department,
            SchoolContent.text_content, SchoolPlacement.stage, SchoolPlacement.grade,
            SchoolPlacement.branch, SchoolPlacement.department, SchoolPlacement.subject,
        ]
        conditions = []
        for term in terms or [nq]:
            if term:
                conditions.extend(column.contains(term) for column in search_columns)
        async with async_session_maker() as s:
            id_stmt = (
                select(SchoolContent.id)
                .join(SchoolPlacement, SchoolPlacement.content_id == SchoolContent.id)
                .where(or_(*conditions) if conditions else True)
                .order_by(SchoolContent.id.desc())
                .distinct()
                .limit(candidate_limit)
            )
            candidate_ids = list((await s.execute(id_stmt)).scalars().all())
            if not candidate_ids:
                return []
            rows = (await s.execute(
                select(SchoolContent, SchoolPlacement)
                .join(SchoolPlacement, SchoolPlacement.content_id == SchoolContent.id)
                .where(SchoolContent.id.in_(candidate_ids))
                .order_by(SchoolContent.id.desc(), SchoolPlacement.id.desc())
            )).all()
        out, seen = [], set()
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

    async def _local_university(self, q, major=None, limit=0, purpose=None):
        candidate_limit = min(max((limit or INTERNAL_RESULT_LIMIT) * 20, 200), 5000)
        terms = tokens(q)[:8]
        resource_cols = [UniversityResource.title, UniversityResource.author, UniversityResource.description, UniversityResource.major, UniversityResource.resource_type]
        project_cols = [UniversityProject.title, UniversityProject.description, UniversityProject.major]
        async with async_session_maker() as s:
            resources = []
            projects = []
            if purpose != 'graduation':
                conditions = [col.contains(term) for term in terms for col in resource_cols if term]
                rid_stmt = (select(UniversityResource.id)
                            .where(UniversityResource.enabled.is_(True), or_(*conditions) if conditions else True)
                            .order_by(UniversityResource.id.desc()).limit(candidate_limit))
                ids = list((await s.execute(rid_stmt)).scalars().all())
                if ids:
                    resources = (await s.execute(select(UniversityResource).where(UniversityResource.id.in_(ids)))).scalars().all()
            if purpose != 'university_search':
                conditions = [col.contains(term) for term in terms for col in project_cols if term]
                pid_stmt = (select(UniversityProject.id)
                            .where(UniversityProject.enabled.is_(True), or_(*conditions) if conditions else True)
                            .order_by(UniversityProject.id.desc()).limit(candidate_limit))
                ids = list((await s.execute(pid_stmt)).scalars().all())
                if ids:
                    projects = (await s.execute(select(UniversityProject).where(UniversityProject.id.in_(ids)))).scalars().all()
        out = []
        for x in resources:
            if purpose == 'graduation':
                continue
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
            if purpose == 'university_search':
                continue
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

    async def search(self, q, scope=None, date_range=None, year=None, limit=10, mode='fast', major=None, level=None, *, section=None, profile=None, purpose=None):
        q = normalize_query(q)
        section = section or ('school' if profile and _profile_value(profile, 'path') == 'school' else 'university')
        # 0 means "as much as practical", never an unbounded network crawl.
        # A finite provider budget keeps Telegram/APIs responsive.
        requested_limit = max(0, int(limit or 0))
        # A caller may request a custom count, but network providers always
        # receive a bounded internal budget to protect the process.
        effective_limit = min(requested_limit, INTERNAL_RESULT_LIMIT) if requested_limit else INTERNAL_RESULT_LIMIT
        provider_limit = effective_limit
        key = (self._cache_version, section, q, scope, date_range, year, requested_limit, mode, major, level, purpose,
               tuple(sorted((k, _profile_value(profile, k)) for k in ('path','stage','grade','branch','department'))))
        self._prune_cache()
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            return [dict(x) for x in cached[1]]

        if section == 'school':
            result = await self._local_school(q, profile, requested_limit)
            self._cache[key] = (time.monotonic(), result)
            return [dict(x) for x in result]

        try:
            corrected = await asyncio.wait_for(ai_service.correct_query(q), timeout=min(SEARCH_TIMEOUT, 10))
        except Exception:
            corrected = q
        corrected = normalize_query(corrected) or q
        queries = expand_query_terms(corrected)
        if q not in queries:
            queries.insert(0, q)

        # University searches are scoped by the student's explicitly selected major.
        # Keep the original query as a candidate for recall, but also send major-aware
        # variants so Telegram/web/API sources can narrow their own result sets.
        if major and section == 'university':
            major_text = normalize_query(str(major))
            scoped = []
            for item in queries:
                if major_text and major_text not in normalize_arabic(item):
                    scoped.append(f"{item} {major_text}")
            queries = scoped + queries
        if mode == 'deep':
            try:
                ai_queries = await asyncio.wait_for(ai_service.expand_query(corrected, major, level), timeout=min(SEARCH_TIMEOUT, 10))
                for item in ai_queries:
                    if item and item not in queries:
                        queries.append(item)
            except Exception:
                pass
        deduped=[]; seen_queries=set()
        for item in queries:
            key=normalize_arabic(item)
            if key and key not in seen_queries:
                seen_queries.add(key); deduped.append(item)
        queries = deduped[:5]

        async def one(src, qq):
            async with search_provider_limit.get():
                try:
                    return await asyncio.wait_for(src.search(qq, scope, date_range, year, provider_limit), timeout=SEARCH_TIMEOUT)
                except Exception:
                    return []
        tasks = [asyncio.create_task(self._local_university(corrected, major=major, limit=effective_limit, purpose=purpose))]
        for src in static_sources():
            for qq in queries:
                tasks.append(asyncio.create_task(one(src, qq)))
        dynamic_task = asyncio.create_task(
            search_dynamic(corrected, scope, year, provider_limit, mode, purpose=purpose, query_variants=queries[:3])
        )
        try:
            fetched = await asyncio.wait_for(asyncio.gather(*tasks, dynamic_task, return_exceptions=True), timeout=max(SEARCH_TIMEOUT * 2, 30))
        except asyncio.TimeoutError:
            for task in tasks + [dynamic_task]:
                if not task.done():
                    task.cancel()
            fetched = await asyncio.gather(*tasks, dynamic_task, return_exceptions=True)
        results = [x for group in fetched if isinstance(group, list) for x in group]

        # The UI exposes rolling calendar-year ranges. Providers differ in their
        # native date-filter capabilities, so enforce the selected range again
        # on normalized result metadata for consistent behavior. Results without
        # a year are excluded when a date filter is explicitly requested.
        if date_range and date_range != 'كل الفترات' and not year:
            range_years = {'آخر سنة': 1, 'آخر 3 سنوات': 3, 'آخر 5 سنوات': 5}.get(date_range)
            if range_years:
                from datetime import datetime, timezone
                current_year = datetime.now(timezone.utc).year
                first_year = current_year - range_years + 1
                filtered = []
                for item in results:
                    try:
                        item_year = int(str(item.get('year') or '').strip()[:4])
                    except (TypeError, ValueError):
                        continue
                    if first_year <= item_year <= current_year:
                        filtered.append(item)
                results = filtered

        scored = []
        for r in results:
            title = str(r.get('title') or '').strip()
            if not title:
                continue
            r['rank_score'] = relevance(
                q, title, r.get('snippet', ''),
                url=r.get('url', ''), source_type=str(r.get('source_type') or ''),
                source_name=str(r.get('source_name') or ''), metadata=r.get('metadata') or {},
            )
            if major and section == 'university':
                major_norm = normalize_arabic(str(major))
                result_text = normalize_arabic(' '.join((title, r.get('snippet', ''), r.get('url', ''), r.get('source_name', ''))))
                if major_norm and major_norm in result_text:
                    r['rank_score'] += 90
            if r['rank_score'] >= 45:
                scored.append(r)

        unique = deduplicate_results(scored)
        unique.sort(key=lambda x: x.get('rank_score', 0), reverse=True)

        if effective_limit > 1 and len(unique) > 1:
            top_score = int(unique[0].get('rank_score', 0) or 0)
            diversity_floor = max(45, int(top_score * 0.75))
            buckets = {}
            for item in unique:
                source = str(item.get('source_name') or item.get('source_type') or 'مصدر غير محدد').strip()
                buckets.setdefault(source, []).append(item)
            for bucket in buckets.values():
                bucket.sort(key=lambda x: x.get('rank_score', 0), reverse=True)

            source_order = sorted(
                buckets,
                key=lambda name: buckets[name][0].get('rank_score', 0),
                reverse=True,
            )
            diversified = []
            # One representative per strong source is preferred, but only if it is
            # close enough to the best score. A weak source cannot displace a much
            # more relevant result simply because it is a different source.
            for source in source_order:
                candidate = buckets[source][0]
                if candidate.get('rank_score', 0) >= diversity_floor:
                    diversified.append(candidate)
                if len(diversified) >= effective_limit:
                    break

            selected = {id(x) for x in diversified}
            if len(diversified) < effective_limit:
                for item in unique:
                    if id(item) not in selected:
                        diversified.append(item)
                        selected.add(id(item))
                        if len(diversified) >= effective_limit:
                            break
            result = diversified[:effective_limit]
        else:
            result = unique[:effective_limit]
        self._cache[key] = (time.monotonic(), result)
        self._prune_cache()
        return [dict(x) for x in result]

search_engine = SearchEngine()
