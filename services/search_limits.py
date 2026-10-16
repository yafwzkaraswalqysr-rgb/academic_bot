from __future__ import annotations

import asyncio
import weakref

from config import SEARCH_CONCURRENCY


class _LoopSemaphoreRegistry:
    def __init__(self, limit: int):
        self.limit = max(1, int(limit))
        self._semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = weakref.WeakKeyDictionary()

    def get(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        semaphore = self._semaphores.get(loop)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self.limit)
            self._semaphores[loop] = semaphore
        return semaphore


search_provider_limit = _LoopSemaphoreRegistry(SEARCH_CONCURRENCY)
