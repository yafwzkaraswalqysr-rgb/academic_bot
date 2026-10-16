from __future__ import annotations
import asyncio
import logging
import re
from urllib.parse import urlparse
from sources.base import BaseSource
from config import TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION, SEARCH_TIMEOUT, INTERNAL_RESULT_LIMIT, MAX_FILE_SIZE

log = logging.getLogger(__name__)


def normalize_telegram_identifier(value: str):
    raw = str(value or '').strip()
    if not raw:
        return None, None
    if re.fullmatch(r'-?\d+', raw):
        return int(raw), None
    if raw.startswith(('http://', 'https://')):
        parsed = urlparse(raw)
        host = (parsed.netloc or '').lower()
        if host not in {'t.me', 'telegram.me', 'www.t.me', 'www.telegram.me'}:
            return None, None
        path = parsed.path.strip('/')
        if not path or path.startswith(('c/', 'joinchat/')):
            return raw, raw
        return path.split('/')[0].lstrip('@'), raw
    username = raw.lstrip('@')
    if re.fullmatch(r'[A-Za-z0-9_]{3,255}', username):
        return username, f'https://t.me/{username}'
    return raw, None


class TelegramSource(BaseSource):
    def __init__(self):
        super().__init__('Telegram', 'telegram')
        self.client = None
        self.authorized = False
        self._lock = asyncio.Lock()

    async def start(self):
        if self.authorized and self.client:
            return True
        if not (TELEGRAM_API_ID and TELEGRAM_API_HASH and TELEGRAM_SESSION):
            return False
        async with self._lock:
            if self.authorized and self.client:
                return True
            client = None
            try:
                from telethon import TelegramClient
                from telethon.sessions import StringSession
                client = TelegramClient(StringSession(TELEGRAM_SESSION), int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
                await asyncio.wait_for(client.connect(), timeout=SEARCH_TIMEOUT)
                authorized = bool(await asyncio.wait_for(client.is_user_authorized(), timeout=SEARCH_TIMEOUT))
                if not authorized:
                    await client.disconnect()
                    return False
                self.client = client
                self.authorized = True
                return True
            except Exception:
                log.exception('Telegram source initialization failed')
                try:
                    await client.disconnect()
                except Exception:
                    pass
                self.client = None
                self.authorized = False
                return False

    async def stop(self):
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self.authorized = False

    async def search_channel(self, identifier, q, limit=0):
        if not await self.start():
            return []
        entity_ref, canonical_url = normalize_telegram_identifier(identifier)
        if entity_ref is None:
            return []
        requested = int(limit or 0)
        target = min(requested, INTERNAL_RESULT_LIMIT) if requested > 0 else INTERNAL_RESULT_LIMIT
        out = []
        try:
            entity = await asyncio.wait_for(self.client.get_entity(entity_ref), timeout=SEARCH_TIMEOUT)
            iterator = self.client.iter_messages(entity, search=q, limit=target)
            deadline = asyncio.get_running_loop().time() + SEARCH_TIMEOUT
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                try:
                    m = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    break
                if not (m.message or m.document):
                    continue
                name = getattr(getattr(m, 'file', None), 'name', None)
                title = name or ((m.message or 'نتيجة Telegram').splitlines()[0][:160])
                username = getattr(entity, 'username', None)
                chat = username or str(getattr(entity, 'id', entity_ref))
                url = f'https://t.me/{chat}/{m.id}' if username else (canonical_url or str(identifier))
                out.append(self._normalize_result({
                    'title': title, 'url': url,
                    'author': getattr(entity, 'title', identifier),
                    'snippet': m.message or '', 'year': m.date.year if m.date else None,
                    'is_downloadable': bool(m.document and (getattr(getattr(m, 'file', None), 'size', None) is None or getattr(getattr(m, 'file', None), 'size', 0) <= MAX_FILE_SIZE)), 'file_url': url,
                    'tg_chat': chat, 'tg_message_id': m.id,
                    'metadata': {'file_name': name, 'file_size': getattr(getattr(m, 'file', None), 'size', None)}
                }))
                if target is not None and len(out) >= target:
                    break
        except asyncio.TimeoutError:
            log.warning('Telegram channel search timed out: %s', identifier)
        except Exception as exc:
            # FloodWait/Rate limits are deliberately not slept through; one
            # source is skipped so the whole search remains responsive.
            if exc.__class__.__name__ == 'FloodWaitError':
                log.warning('Telegram FloodWait for %s; source skipped.', identifier)
            else:
                log.exception('Telegram channel search failed: %s', identifier)
        return out

    async def search(self, query, scope=None, date_range=None, year=None, limit=10):
        return []

telegram_source = TelegramSource()
