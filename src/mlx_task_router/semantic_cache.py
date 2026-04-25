"""Semantic response cache — similarity-based matching instead of exact-match.

Uses character-level n-gram similarity (no external dependencies) to find
similar previous requests and return cached responses. Falls back gracefully
when similarity is below threshold.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any


_DEFAULT_THRESHOLD = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.85"))
_DEFAULT_TTL = int(os.getenv("SEMANTIC_CACHE_TTL", "120"))
_DEFAULT_MAX = int(os.getenv("SEMANTIC_CACHE_MAX_ENTRIES", "200"))


def _ngram_set(text: str, n: int = 3) -> set[str]:
    """Generate character-level n-gram set for similarity comparison."""
    text = text.lower().strip()
    if len(text) < n:
        return {text}
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def _similarity(a: str, b: str) -> float:
    """Compute Jaccard similarity between two strings using character n-grams."""
    if a == b:
        return 1.0
    set_a = _ngram_set(a)
    set_b = _ngram_set(b)
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


class _CacheEntry:
    __slots__ = ("text", "tool_key", "value", "timestamp", "ngrams")

    def __init__(self, text: str, tool_key: str, value: Any, timestamp: float):
        self.text = text
        self.tool_key = tool_key
        self.value = value
        self.timestamp = timestamp
        self.ngrams = _ngram_set(text)


class SemanticCache:
    def __init__(
        self,
        threshold: float = _DEFAULT_THRESHOLD,
        ttl: int = _DEFAULT_TTL,
        max_entries: int = _DEFAULT_MAX,
    ):
        self._entries: list[_CacheEntry] = []
        self._lock = threading.Lock()
        self._threshold = threshold
        self._ttl = ttl
        self._max = max_entries
        self._hits = 0
        self._misses = 0
        self._near_misses = 0

    @property
    def threshold(self) -> float:
        return self._threshold

    def get(self, text: str, tool_names: list[str] | None = None) -> Any | None:
        tool_key = ",".join(sorted(tool_names)) if tool_names else ""
        query_ngrams = _ngram_set(text)
        now = time.time()

        with self._lock:
            best_score = 0.0
            best_entry: _CacheEntry | None = None

            for entry in self._entries:
                if now - entry.timestamp > self._ttl:
                    continue
                if entry.tool_key != tool_key:
                    continue

                # Fast exact match
                if entry.text == text:
                    self._hits += 1
                    return entry.value

                # Similarity check via pre-computed n-grams
                if not query_ngrams or not entry.ngrams:
                    continue
                intersection = len(query_ngrams & entry.ngrams)
                union = len(query_ngrams | entry.ngrams)
                score = intersection / union if union > 0 else 0.0

                if score > best_score:
                    best_score = score
                    best_entry = entry

            if best_entry and best_score >= self._threshold:
                self._hits += 1
                return best_entry.value

            if best_entry and best_score >= self._threshold * 0.8:
                self._near_misses += 1

            self._misses += 1
            return None

    def put(self, text: str, value: Any, tool_names: list[str] | None = None) -> None:
        tool_key = ",".join(sorted(tool_names)) if tool_names else ""
        entry = _CacheEntry(text, tool_key, value, time.time())

        with self._lock:
            # Evict expired entries
            now = time.time()
            self._entries = [e for e in self._entries if now - e.timestamp <= self._ttl]

            if len(self._entries) >= self._max:
                self._entries.pop(0)
            self._entries.append(entry)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "near_misses": self._near_misses,
                "hit_rate": f"{self._hits / total * 100:.0f}%" if total > 0 else "0%",
                "entries": len(self._entries),
                "threshold": self._threshold,
                "ttl": self._ttl,
            }

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0
            self._near_misses = 0


semantic_cache = SemanticCache()
