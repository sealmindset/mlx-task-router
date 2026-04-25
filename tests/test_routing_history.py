"""Tests for routing decision history ring buffer."""

from __future__ import annotations

from mlx_task_router.routing_history import RoutingHistory


class TestRoutingHistory:
    def test_record_and_get(self):
        rh = RoutingHistory(max_entries=10)
        rh.record("local", "fwd=0.20 [short -0.1]", "exec:git", "git status", "test-model")
        history = rh.get_history()
        assert len(history) == 1
        assert history[0]["route"] == "local"
        assert history[0]["message_preview"] == "git status"

    def test_ring_buffer_eviction(self):
        rh = RoutingHistory(max_entries=3)
        for i in range(5):
            rh.record("local", f"fwd=0.{i}0", "", f"msg {i}", "m")
        history = rh.get_history()
        assert len(history) == 3
        assert "msg 4" in history[0]["message_preview"]

    def test_most_recent_first(self):
        rh = RoutingHistory(max_entries=10)
        rh.record("local", "fwd=0.10", "", "first", "m")
        rh.record("forward", "fwd=0.60", "", "second", "m")
        history = rh.get_history()
        assert history[0]["route"] == "forward"
        assert history[1]["route"] == "local"

    def test_limit_parameter(self):
        rh = RoutingHistory(max_entries=100)
        for i in range(20):
            rh.record("local", "fwd=0.10", "", f"msg {i}", "m")
        history = rh.get_history(limit=5)
        assert len(history) == 5

    def test_summary(self):
        rh = RoutingHistory(max_entries=100)
        rh.record("local", "fwd=0.10", "", "a", "m")
        rh.record("local", "fwd=0.20", "", "b", "m")
        rh.record("forward", "fwd=0.60", "", "c", "m")
        s = rh.summary()
        assert s["total"] == 3
        assert s["local"] == 2
        assert s["forward"] == 1

    def test_clear(self):
        rh = RoutingHistory(max_entries=10)
        rh.record("local", "fwd=0.10", "", "a", "m")
        rh.clear()
        assert rh.get_history() == []

    def test_parses_forward_score(self):
        rh = RoutingHistory(max_entries=10)
        rh.record("forward", "fwd=0.75 [complex:'explain' +0.5, long(600ch) +0.2]", "complex:explain", "explain this", "m")
        entry = rh.get_history()[0]
        assert entry["forward_score"] == 0.75
        assert len(entry["signals"]) > 0

    def test_preview_truncation(self):
        rh = RoutingHistory(max_entries=10)
        long_msg = "x" * 200
        rh.record("local", "fwd=0.10", "", long_msg, "m")
        entry = rh.get_history()[0]
        assert len(entry["message_preview"]) <= 84
        assert entry["message_preview"].endswith("...")
