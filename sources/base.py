from __future__ import annotations
from abc import ABC,abstractmethod
class BaseSource(ABC):
    def __init__(self,name,source_type): self.name=name; self.source_type=source_type; self.is_active=True
    @abstractmethod
    async def search(self,query,scope=None,date_range=None,year=None,limit=10): ...
    def _normalize_result(self,d):
        return {'title':str(d.get('title') or '').strip(),'url':d.get('url') or '','author':d.get('author') or '', 'year':d.get('year'),'snippet':str(d.get('snippet') or '')[:1000], 'source_name':self.name,'source_type':self.source_type,'file_url':d.get('file_url') or '', 'is_downloadable':bool(d.get('is_downloadable')),'tg_chat':d.get('tg_chat'),'tg_message_id':d.get('tg_message_id'),'metadata':d.get('metadata') or {}}
