"""Tests for per-session routing statistics."""

from __future__ import annotations

import time

from mlx_task_router.session_stats import SessionStats, SessionTracker


class TestSessionStats:
    def test_record_local(self):
        ss = SessionStats(session_id="test-1")
        ss.record(route="local", trigger="exec:git", forward_score=0.0, message_preview="git status")
        assert ss.requests_total == 1
        assert ss.requests_local == 1
        assert ss.requests_forwarded == 0
        assert ss.top_triggers["exec:git"] == 1

    def test_record_forward(self):
        ss = SessionStats(session_id="test-2")
        ss.record(route="forward", trigger="complex:explain", forward_score=0.7, message_preview="explain this")
        assert ss.requests_total == 1
        assert ss.requests_forwarded == 1
        assert ss.requests_local == 0

    def test_record_cache(self):
        ss = SessionStats(session_id="test-3")
        ss.record(route="cache", trigger="", forward_score=0.0, message_preview="git status")
        assert ss.requests_cache == 1
        assert ss.requests_local == 0
        assert ss.requests_forwarded == 0

    def test_local_pct(self):
        ss = SessionStats(session_id="test-4")
        ss.record(route="local", trigger="", forward_score=0.0, message_preview="a")
        ss.record(route="local", trigger="", forward_score=0.0, message_preview="b")
        ss.record(route="forward", trigger="", forward_score=0.0, message_preview="c")
        assert ss.local_pct == 66.7

    def test_local_pct_empty(self):
        ss = SessionStats(session_id="test-5")
        assert ss.local_pct == 0.0

    def test_duration(self):
        ss = SessionStats(session_id="test-6")
        ss.started_at = 1000.0
        ss.last_activity = 1060.5
        assert ss.duration_seconds == 60.5

    def test_to_dict_keys(self):
        ss = SessionStats(session_id="test-7")
        ss.record(route="local", trigger="exec:git", forward_score=-0.3, message_preview="git status")
        d = ss.to_dict()
        assert d["session_id"] == "test-7"
        assert d["requests_total"] == 1
        assert "recent_decisions" in d
        assert len(d["recent_decisions"]) == 1
        assert d["recent_decisions"][0]["route"] == "local"

    def test_decisions_capped(self):
        ss = SessionStats(session_id="test-8")
        for i in range(60):
            ss.record(route="local", trigger="", forward_score=0.0, message_preview=f"msg {i}")
        assert len(ss.decisions) == 50  # _MAX_DECISIONS_PER_SESSION

    def test_top_triggers_sorted(self):
        ss = SessionStats(session_id="test-9")
        for _ in range(5):
            ss.record(route="local", trigger="exec:git", forward_score=0.0, message_preview="a")
        for _ in range(3):
            ss.record(route="forward", trigger="complex:explain", forward_score=0.6, message_preview="b")
        for _ in range(1):
            ss.record(route="local", trigger="action:run the tests", forward_score=-0.3, message_preview="c")
        d = ss.to_dict()
        triggers = list(d["top_triggers"].keys())
        assert triggers[0] == "exec:git"
        assert triggers[1] == "complex:explain"


class TestSessionTracker:
    def test_auto_session(self):
        st = SessionTracker(gap_seconds=300)
        sid = st.record(route="local", trigger="", forward_score=0.0, message_preview="hi")
        assert sid == "auto-1"

    def test_header_session(self):
        st = SessionTracker()
        sid = st.record(
            route="local", trigger="", forward_score=0.0, message_preview="hi",
            headers={"x-session-id": "my-session-42"},
        )
        assert sid == "my-session-42"

    def test_anthropic_session_header(self):
        st = SessionTracker()
        sid = st.record(
            route="local", trigger="", forward_score=0.0, message_preview="hi",
            headers={"anthropic-session-id": "abc-123"},
        )
        assert sid == "abc-123"

    def test_request_id_prefix(self):
        st = SessionTracker()
        sid = st.record(
            route="local", trigger="", forward_score=0.0, message_preview="hi",
            headers={"x-request-id": "session-42-req-7"},
        )
        assert sid == "session-42-req"

    def test_auto_session_gap(self):
        st = SessionTracker(gap_seconds=5)
        st.record(route="local", trigger="", forward_score=0.0, message_preview="a")
        assert st._current_auto_id == "auto-1"
        # Simulate gap
        st._last_activity = time.time() - 10
        st.record(route="local", trigger="", forward_score=0.0, message_preview="b")
        assert st._current_auto_id == "auto-2"

    def test_same_auto_session_within_gap(self):
        st = SessionTracker(gap_seconds=300)
        sid1 = st.record(route="local", trigger="", forward_score=0.0, message_preview="a")
        sid2 = st.record(route="local", trigger="", forward_score=0.0, message_preview="b")
        assert sid1 == sid2

    def test_get_session(self):
        st = SessionTracker()
        st.record(
            route="local", trigger="exec:git", forward_score=-0.3, message_preview="git status",
            headers={"x-session-id": "sess-1"},
        )
        s = st.get_session("sess-1")
        assert s is not None
        assert s["requests_total"] == 1

    def test_get_session_not_found(self):
        st = SessionTracker()
        assert st.get_session("nonexistent") is None

    def test_get_current_session(self):
        st = SessionTracker()
        st.record(route="local", trigger="", forward_score=0.0, message_preview="a",
                   headers={"x-session-id": "first"})
        st.record(route="local", trigger="", forward_score=0.0, message_preview="b",
                   headers={"x-session-id": "second"})
        current = st.get_current_session()
        assert current["session_id"] == "second"

    def test_get_current_session_empty(self):
        st = SessionTracker()
        assert st.get_current_session() is None

    def test_get_all_sessions(self):
        st = SessionTracker()
        for i in range(5):
            st.record(route="local", trigger="", forward_score=0.0, message_preview=f"msg {i}",
                       headers={"x-session-id": f"s-{i}"})
        sessions = st.get_all_sessions(limit=3)
        assert len(sessions) == 3
        assert sessions[0]["session_id"] == "s-4"  # most recent first

    def test_max_sessions_eviction(self):
        st = SessionTracker(max_sessions=3)
        for i in range(5):
            st.record(route="local", trigger="", forward_score=0.0, message_preview=f"msg {i}",
                       headers={"x-session-id": f"s-{i}"})
        assert len(st._sessions) == 3
        assert st.get_session("s-0") is None
        assert st.get_session("s-1") is None
        assert st.get_session("s-4") is not None

    def test_summary(self):
        st = SessionTracker(gap_seconds=300)
        st.record(route="local", trigger="", forward_score=0.0, message_preview="a",
                   headers={"x-session-id": "active"})
        s = st.summary()
        assert s["total_sessions"] == 1
        assert s["active_sessions"] == 1
        assert s["current_session"] == "active"

    def test_summary_empty(self):
        st = SessionTracker()
        s = st.summary()
        assert s["total_sessions"] == 0

    def test_clear(self):
        st = SessionTracker()
        st.record(route="local", trigger="", forward_score=0.0, message_preview="a")
        st.clear()
        assert st.get_current_session() is None
        assert st.summary()["total_sessions"] == 0
