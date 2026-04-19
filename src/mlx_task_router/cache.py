"""Response cache for locally-routed requests."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from typing import Any


class ResponseCache:
    def __init__(self):
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._ttl = int(os.getenv("CACHE_TTL", "60"))
        self._max_entries = int(os.getenv("CACHE_MAX_ENTRIES", "100"))
        self._hits = 0
        self._misses = 0

    @property
    def ttl(self) -> int:
        return self._ttl

    def _make_key(self, latest_text: str, tool_names: list[str] | None = None) -> str:
        parts = [latest_text]
        if tool_names:
            parts.append(",".join(sorted(tool_names)))
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def get(self, latest_text: str, tool_names: list[str] | None = None) -> Any | None:
        key = self._make_key(latest_text, tool_names)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            ts, value = entry
            if time.time() - ts > self._ttl:
                del self._cache[key]
                self._misses += 1
                return None
            self._hits += 1
            return value

    def put(self, latest_text: str, value: Any, tool_names: list[str] | None = None) -> None:
        key = self._make_key(latest_text, tool_names)
        with self._lock:
            if len(self._cache) >= self._max_entries:
                self._evict()
            self._cache[key] = (time.time(), value)

    def _evict(self) -> None:
        if not self._cache:
            return
        oldest_key = min(self._cache, key=lambda k: self._cache[k][0])
        del self._cache[oldest_key]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{self._hits / total * 100:.0f}%" if total > 0 else "0%",
                "entries": len(self._cache),
                "ttl": self._ttl,
            }

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0


response_cache = ResponseCache()
