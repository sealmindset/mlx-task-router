from __future__ import annotations

import time
from unittest.mock import patch

from mlx_task_router.cache import ResponseCache


class TestResponseCache:
    def test_put_and_get(self):
        cache = ResponseCache()
        cache.put("hello", {"result": "world"})
        assert cache.get("hello") == {"result": "world"}

    def test_miss(self):
        cache = ResponseCache()
        assert cache.get("nonexistent") is None

    def test_ttl_expiry(self):
        cache = ResponseCache()
        cache._ttl = 0
        cache.put("key", "value")
        time.sleep(0.01)
        assert cache.get("key") is None

    def test_tool_names_affect_key(self):
        cache = ResponseCache()
        cache.put("hello", "v1", tool_names=["Bash"])
        cache.put("hello", "v2", tool_names=["Read"])
        assert cache.get("hello", tool_names=["Bash"]) == "v1"
        assert cache.get("hello", tool_names=["Read"]) == "v2"

    def test_eviction_on_max(self):
        cache = ResponseCache()
        cache._max_entries = 2
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        assert cache.get("a") is None
        assert cache.get("c") == 3

    def test_stats(self):
        cache = ResponseCache()
        cache.put("x", "val")
        cache.get("x")
        cache.get("y")
        s = cache.stats()
        assert s["hits"] == 1
        assert s["misses"] == 1
        assert s["entries"] == 1

    def test_clear(self):
        cache = ResponseCache()
        cache.put("a", 1)
        cache.get("a")
        cache.clear()
        s = cache.stats()
        assert s["hits"] == 0
        assert s["entries"] == 0

    def test_hit_rate_display(self):
        cache = ResponseCache()
        cache.put("a", 1)
        cache.get("a")
        cache.get("a")
        cache.get("miss")
        s = cache.stats()
        assert s["hit_rate"] == "67%"
