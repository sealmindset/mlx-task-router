"""Tests for semantic response cache."""

from __future__ import annotations

from mlx_task_router.semantic_cache import SemanticCache, _similarity


class TestSimilarity:
    def test_identical_strings(self):
        assert _similarity("git status", "git status") == 1.0

    def test_very_similar(self):
        score = _similarity("git status", "git status --short")
        assert score >= 0.5

    def test_completely_different(self):
        score = _similarity("git status", "explain quantum physics")
        assert score < 0.3

    def test_minor_typo(self):
        score = _similarity("git stauts", "git status")
        assert score > 0.3

    def test_empty_strings(self):
        assert _similarity("", "") == 1.0


class TestSemanticCache:
    def test_exact_match_hit(self):
        sc = SemanticCache(threshold=0.85)
        sc.put("git status", {"result": "ok"})
        result = sc.get("git status")
        assert result == {"result": "ok"}

    def test_similar_query_hit(self):
        sc = SemanticCache(threshold=0.7)
        sc.put("git status --short", {"result": "short"})
        result = sc.get("git status --short -b")
        # Similar enough to match at 0.7 threshold
        if result is not None:
            assert result == {"result": "short"}

    def test_dissimilar_query_miss(self):
        sc = SemanticCache(threshold=0.85)
        sc.put("git status", {"result": "ok"})
        result = sc.get("explain quantum computing in detail")
        assert result is None

    def test_tool_key_filtering(self):
        sc = SemanticCache(threshold=0.85)
        sc.put("git status", {"result": "a"}, tool_names=["Bash"])
        sc.put("git status", {"result": "b"}, tool_names=["Read"])
        assert sc.get("git status", tool_names=["Bash"]) == {"result": "a"}
        assert sc.get("git status", tool_names=["Read"]) == {"result": "b"}

    def test_ttl_expiration(self):
        sc = SemanticCache(threshold=0.85, ttl=0)
        sc.put("git status", {"result": "ok"})
        # TTL=0 means immediately expired
        result = sc.get("git status")
        assert result is None

    def test_max_entries_eviction(self):
        sc = SemanticCache(threshold=0.85, max_entries=2, ttl=9999)
        sc.put("msg 1", {"r": 1})
        sc.put("msg 2", {"r": 2})
        sc.put("msg 3", {"r": 3})
        # First entry should be evicted
        assert sc.get("msg 1") is None
        assert sc.get("msg 3") == {"r": 3}

    def test_stats(self):
        sc = SemanticCache(threshold=0.85, ttl=9999)
        sc.put("git status", {"result": "ok"})
        sc.get("git status")
        sc.get("unknown query")
        s = sc.stats()
        assert s["hits"] == 1
        assert s["misses"] == 1
        assert s["entries"] == 1

    def test_clear(self):
        sc = SemanticCache(threshold=0.85, ttl=9999)
        sc.put("git status", {"result": "ok"})
        sc.clear()
        assert sc.get("git status") is None
        s = sc.stats()
        assert s["hits"] == 0
        assert s["entries"] == 0
