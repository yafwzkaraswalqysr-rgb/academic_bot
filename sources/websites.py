from __future__ import annotations
import aiohttp
import json
import logging
from urllib.parse import quote_plus, urljoin
from bs4 import BeautifulSoup
from sources.base import BaseSource
from utils.security import is_ssrf_safe_url
from config import HTTP_TIMEOUT

log = logging.getLogger(__name__)
PAGE_SIZE = 100  # API/page size only

class SemanticScholarSource(BaseSource):
    def __init__(self): super().__init__('Semantic Scholar', 'api')

    async def search(self, q, scope=None, date_range=None, year=None, limit=10):
        target = None if not limit or limit <= 0 else int(limit)
        out = []
        offset = 0
        try:
            while target is None or len(out) < target:
                page = PAGE_SIZE if target is None else min(PAGE_SIZE, target - len(out))
                params = {'query': q, 'limit': page, 'offset': offset,
                          'fields': 'title,authors,year,abstract,url,externalIds,venue'}
                if year: params['year'] = year
                async with aiohttp.ClientSession() as s:
                    async with s.get('https://api.semanticscholar.org/graph/v1/paper/search', params=params, timeout=HTTP_TIMEOUT) as r:
                        if r.status != 200:
                            return out
                        d = await r.json()
                batch = d.get('data', []) or []
                for x in batch:
                    out.append(self._normalize_result({
                        'title': x.get('title'), 'url': x.get('url'),
                        'author': ', '.join(a.get('name', '') for a in x.get('authors', [])[:3]),
                        'year': x.get('year'), 'snippet': x.get('abstract', ''),
                        'metadata': {'doi': x.get('externalIds', {}).get('DOI')}
                    }))
                if not batch or len(batch) < page:
                    break
                offset += len(batch)
                total = d.get('total')
                if total is not None and offset >= int(total):
                    break
            return out if target is None else out[:target]
        except Exception:
            log.exception('Semantic Scholar search failed')
            return out

class CrossRefSource(BaseSource):
    def __init__(self): super().__init__('CrossRef', 'api')

    async def search(self, q, scope=None, date_range=None, year=None, limit=10):
        target = None if not limit or limit <= 0 else int(limit)
        out = []
        offset = 0
        try:
            while target is None or len(out) < target:
                rows = 1000 if target is None else min(1000, target - len(out))
                params = {'query': q, 'rows': rows, 'offset': offset}
                if year: params['filter'] = f'from-pub-date:{year}-01-01,until-pub-date:{year}-12-31'
                async with aiohttp.ClientSession() as s:
                    async with s.get('https://api.crossref.org/works', params=params, timeout=HTTP_TIMEOUT) as r:
                        if r.status != 200:
                            return out
                        d = await r.json()
                batch = d.get('message', {}).get('items', []) or []
                for x in batch:
                    authors = ', '.join((a.get('given', '') + ' ' + a.get('family', '')).strip() for a in x.get('author', [])[:3])
                    out.append(self._normalize_result({
                        'title': (x.get('title') or [''])[0], 'url': x.get('URL'), 'author': authors,
                        'year': ((x.get('published-print') or x.get('published-online') or {}).get('date-parts') or [[None]])[0][0],
                        'metadata': {'doi': x.get('DOI')}
                    }))
                if not batch or len(batch) < rows:
                    break
                offset += len(batch)
                total = d.get('message', {}).get('total-results')
                if total is not None and offset >= int(total):
                    break
            return out if target is None else out[:target]
        except Exception:
            log.exception('CrossRef search failed')
            return out

class GenericWebSource(BaseSource):
    def __init__(self, name, search_url, mapping=None, headers=None):
        super().__init__(name, 'website')
        self.search_url = search_url
        self.mapping = mapping or {}
        self.headers = headers or {}

    async def search(self, q, scope=None, date_range=None, year=None, limit=10):
        url = self.search_url.format(query=quote_plus(q), q=quote_plus(q))
        if not is_ssrf_safe_url(url):
            return []
        try:
            async with aiohttp.ClientSession(headers=self.headers) as s:
                async with s.get(url, timeout=HTTP_TIMEOUT, allow_redirects=False) as r:
                    if r.status != 200:
                        return []
                    html = await r.text(errors='ignore')
            soup = BeautifulSoup(html, 'lxml')
            selectors = self.mapping.get('item', 'article,.result-item,.search-result')
            items = soup.select(selectors)
            out = []
            for item in items:
                t = item.select_one(self.mapping.get('title', 'h1,h2,h3,.title'))
                a = item.select_one(self.mapping.get('url', 'a[href]'))
                sn = item.select_one(self.mapping.get('snippet', '.snippet,.description,p'))
                if t and a:
                    href = urljoin(url, a.get('href', ''))
                    if not href.startswith(('http://', 'https://')):
                        continue
                    out.append(self._normalize_result({'title': t.get_text(' ', strip=True), 'url': href, 'snippet': sn.get_text(' ', strip=True) if sn else ''}))
            return out if not limit or limit <= 0 else out[:int(limit)]
        except Exception:
            log.exception('%s search failed', self.name)
            return []

semantic_scholar = SemanticScholarSource()
crossref = CrossRefSource()
