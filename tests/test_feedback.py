from __future__ import annotations

from mlx_task_router.feedback import RoutingFeedback


class TestRoutingFeedback:
    def test_no_penalty_below_min_attempts(self):
        fb = RoutingFeedback()
        fb._triggers = {"exec:git": {"attempts": 1, "failures": 1}}
        assert fb.penalty("exec:git") == 0.0

    def test_no_penalty_low_failure_rate(self):
        fb = RoutingFeedback()
        fb._triggers = {"exec:git": {"attempts": 10, "failures": 2}}
        assert fb.penalty("exec:git") == 0.0

    def test_penalty_high_failure_rate(self):
        fb = RoutingFeedback()
        fb._triggers = {"exec:git": {"attempts": 10, "failures": 5}}
        penalty = fb.penalty("exec:git")
        assert penalty < 0
        assert penalty == -0.4 * 0.5

    def test_penalty_100_percent_failure(self):
        fb = RoutingFeedback()
        fb._triggers = {"exec:git": {"attempts": 5, "failures": 5}}
        assert fb.penalty("exec:git") == -0.4

    def test_record_success(self):
        fb = RoutingFeedback()
        fb.record_success("exec:ls")
        assert fb._triggers["exec:ls"]["attempts"] == 1
        assert fb._triggers["exec:ls"]["failures"] == 0

    def test_record_failure(self):
        fb = RoutingFeedback()
        fb.record_failure("exec:ls")
        assert fb._triggers["exec:ls"]["attempts"] == 1
        assert fb._triggers["exec:ls"]["failures"] == 1

    def test_unknown_trigger_no_penalty(self):
        fb = RoutingFeedback()
        assert fb.penalty("unknown") == 0.0

    def test_reset(self):
        fb = RoutingFeedback()
        fb.record_success("exec:git")
        fb.reset()
        assert fb._triggers == {}

    def test_stats(self):
        fb = RoutingFeedback()
        fb._triggers = {"exec:git": {"attempts": 10, "failures": 4}}
        s = fb.stats()
        assert "exec:git" in s
        assert s["exec:git"]["failure_rate"] == "40%"
