import aiohttp
import logging
from sources.base import BaseSource
from config import HTTP_TIMEOUT

log = logging.getLogger(__name__)
PAGE_SIZE = 100  # provider page size, not a total-result cap

class OpenAlexSource(BaseSource):
    def __init__(self):
        super().__init__('OpenAlex', 'api')

    async def search(self, q, scope=None, date_range=None, year=None, limit=10):
        target = None if not limit or limit <= 0 else int(limit)
        out = []
        cursor = '*'
        try:
            while target is None or len(out) < target:
                per_page = PAGE_SIZE if target is None else min(PAGE_SIZE, target - len(out))
                params = {'search': q, 'per-page': per_page, 'cursor': cursor}
                if year:
                    params['filter'] = f'from_publication_date:{year}-01-01,to_publication_date:{year}-12-31'
                async with aiohttp.ClientSession() as s:
                    async with s.get('https://api.openalex.org/works', params=params, timeout=HTTP_TIMEOUT) as r:
                        if r.status != 200:
                            return out
                        data = await r.json()
                batch = data.get('results', []) or []
                if not batch:
                    break
                for x in batch:
                    out.append(self._normalize_result({
                        'title': x.get('display_name'),
                        'url': x.get('primary_location', {}).get('landing_page_url') or x.get('id'),
                        'author': ', '.join(a.get('author', {}).get('display_name', '') for a in x.get('authorships', [])[:3]),
                        'year': x.get('publication_year'),
                        'snippet': x.get('abstract_inverted_index') and 'مستخلص متاح في المصدر',
                        'metadata': {'doi': x.get('doi')}
                    }))
                next_cursor = (data.get('meta') or {}).get('next_cursor')
                if not next_cursor or next_cursor == cursor or len(batch) < per_page:
                    break
                cursor = next_cursor
            return out if target is None else out[:target]
        except Exception:
            log.exception('OpenAlex search failed')
            return out

openalex = OpenAlexSource()
