"""Simple asyncio-aware TTL cache.

- `get_or_fetch(key, fetch_coro)` returns the cached value if still fresh,
  otherwise runs `fetch_coro()` under a per-key lock (so concurrent callers
  for the same key share a single upstream fetch).
- `clear(key=None)` drops entries.

Values are wrapped with their timestamp so callers can expose `cached_at`.
"""
import asyncio
import time
from typing import Any, Awaitable, Callable


class TTLCache:
    def __init__(self, ttl_sec: float):
        self.ttl_sec = float(ttl_sec)
        self._entries: dict[Any, tuple[Any, float]] = {}
        self._locks: dict[Any, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    def _is_fresh(self, cached_at: float, now: float) -> bool:
        return (now - cached_at) < self.ttl_sec

    async def _get_lock(self, key: Any) -> asyncio.Lock:
        async with self._global_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    def peek(self, key: Any) -> tuple[Any, float] | None:
        """Return (value, cached_at) if present (ignoring TTL); else None."""
        return self._entries.get(key)

    async def get_or_fetch(
        self,
        key: Any,
        fetch_coro: Callable[[], Awaitable[Any]],
    ) -> tuple[Any, float]:
        """Return (value, cached_at). Runs fetch_coro() under a per-key lock
        when stale/missing."""
        now = time.time()
        entry = self._entries.get(key)
        if entry is not None and self._is_fresh(entry[1], now):
            return entry

        lock = await self._get_lock(key)
        async with lock:
            # re-check after acquiring the lock (another waiter may have filled it)
            now = time.time()
            entry = self._entries.get(key)
            if entry is not None and self._is_fresh(entry[1], now):
                return entry

            value = await fetch_coro()
            cached_at = time.time()
            self._entries[key] = (value, cached_at)
            return value, cached_at

    def clear(self, key: Any | None = None) -> None:
        if key is None:
            self._entries.clear()
            return
        self._entries.pop(key, None)
