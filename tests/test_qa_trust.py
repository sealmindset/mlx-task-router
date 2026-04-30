"""Tests for QA Trust — graduated trust per request category."""

import os
import json
import tempfile
from unittest.mock import patch

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from mlx_task_router.qa_trust import QATrust, CategoryEvidence, TrustLevel, _EVIDENCE_FILE
from mlx_task_router.config import config


class TestCategoryEvidence:
    def test_initial_state(self):
        ev = CategoryEvidence(category="test")
        assert ev.total_samples == 0
        assert ev.pass_rate == 0.0
        assert ev.trust_level == TrustLevel.UNPROVEN
        assert ev.confidence_interval_95 == (0.0, 0.0)

    def test_record_pass(self):
        ev = CategoryEvidence(category="test")
        ev.record(passed=True, score=5)
        assert ev.total_samples == 1
        assert ev.pass_count == 1
        assert ev.pass_rate == 1.0
        assert ev.last_pass_ts > 0

    def test_record_fail(self):
        ev = CategoryEvidence(category="test")
        ev.record(passed=False, score=2)
        assert ev.total_samples == 1
        assert ev.fail_count == 1
        assert ev.pass_rate == 0.0
        assert ev.last_fail_ts > 0

    def test_recent_scores_capped(self):
        ev = CategoryEvidence(category="test")
        for i in range(60):
            ev.record(passed=True, score=5)
        assert len(ev.recent_scores) == 50

    def test_trust_level_unproven(self):
        ev = CategoryEvidence(category="test")
        for _ in range(19):
            ev.record(passed=True)
        assert ev.trust_level == TrustLevel.UNPROVEN

    def test_trust_level_building(self):
        ev = CategoryEvidence(category="test")
        for _ in range(25):
            ev.record(passed=True)
        # 25 samples, 100% pass → building (not enough for trusted)
        assert ev.trust_level == TrustLevel.BUILDING

    def test_trust_level_trusted(self):
        ev = CategoryEvidence(category="test")
        for _ in range(config.qa_trust_min_samples):
            ev.record(passed=True)
        assert ev.trust_level == TrustLevel.TRUSTED

    def test_trust_level_proven(self):
        ev = CategoryEvidence(category="test")
        for _ in range(config.qa_trust_proven_samples):
            ev.record(passed=True)
        assert ev.trust_level == TrustLevel.PROVEN

    def test_trust_level_degraded(self):
        ev = CategoryEvidence(category="test")
        for _ in range(15):
            ev.record(passed=True)
        for _ in range(10):
            ev.record(passed=False)
        # 25 samples, 60% pass → degraded
        assert ev.trust_level == TrustLevel.DEGRADED

    def test_confidence_interval(self):
        ev = CategoryEvidence(category="test")
        for _ in range(100):
            ev.record(passed=True)
        ci = ev.confidence_interval_95
        assert ci[0] > 0.95
        assert ci[1] == 1.0

    def test_to_dict(self):
        ev = CategoryEvidence(category="git_commands")
        ev.record(passed=True, score=5)
        d = ev.to_dict()
        assert d["category"] == "git_commands"
        assert d["trust_level"] == "unproven"
        assert d["total_samples"] == 1
        assert "confidence_interval_95" in d


class TestQATrust:
    def _make_trust(self) -> QATrust:
        """Create a fresh QATrust that doesn't load from disk."""
        t = QATrust.__new__(QATrust)
        import threading
        t._lock = threading.Lock()
        t._categories = {}
        t._total_gated = 0
        t._total_bypassed = 0
        t._total_swapped = 0
        t._shadow_cost_tokens = 0
        return t

    def test_record_outcome(self):
        t = self._make_trust()
        t.record_outcome("git_commands", passed=True, score=5)
        assert t._total_gated == 1
        assert "git_commands" in t._categories
        assert t._categories["git_commands"].pass_count == 1

    def test_record_swap(self):
        t = self._make_trust()
        t.record_outcome("code_generation", passed=False, score=2, swapped=True)
        assert t._total_swapped == 1

    def test_record_bypass(self):
        t = self._make_trust()
        t.record_bypass()
        assert t._total_bypassed == 1

    def test_should_gate_disabled(self):
        t = self._make_trust()
        with patch.object(config, "qa_gate_enabled", False):
            assert t.should_gate(0.5) is False

    def test_should_gate_enabled_in_zone(self):
        t = self._make_trust()
        with patch.object(config, "qa_gate_enabled", True), \
             patch.object(config, "qa_gate_lower", 0.3), \
             patch.object(config, "qa_gate_upper", 0.7):
            assert t.should_gate(0.5) is True

    def test_should_gate_below_zone(self):
        t = self._make_trust()
        with patch.object(config, "qa_gate_enabled", True), \
             patch.object(config, "qa_gate_lower", 0.3), \
             patch.object(config, "qa_gate_upper", 0.7):
            assert t.should_gate(0.2) is False

    def test_should_gate_above_zone(self):
        t = self._make_trust()
        with patch.object(config, "qa_gate_enabled", True), \
             patch.object(config, "qa_gate_lower", 0.3), \
             patch.object(config, "qa_gate_upper", 0.7):
            assert t.should_gate(0.8) is False

    def test_proven_category_skips_gate(self):
        t = self._make_trust()
        # Build a proven category
        for _ in range(config.qa_trust_proven_samples):
            t.record_outcome("git_commands", passed=True, score=5)

        with patch.object(config, "qa_gate_enabled", True), \
             patch.object(config, "qa_gate_lower", 0.3), \
             patch.object(config, "qa_gate_upper", 0.7):
            # Proven category should skip gate even in the zone
            assert t.should_gate(0.5, category="git_commands") is False

    def test_get_gate_bounds_default(self):
        t = self._make_trust()
        with patch.object(config, "qa_gate_lower", 0.3), \
             patch.object(config, "qa_gate_upper", 0.7):
            lower, upper = t.get_gate_bounds()
            assert lower == 0.3
            assert upper == 0.7

    def test_get_gate_bounds_with_override(self):
        t = self._make_trust()
        # Build a trusted category (narrows bounds by ±0.1)
        for _ in range(config.qa_trust_min_samples):
            t.record_outcome("shell_commands", passed=True, score=5)

        with patch.object(config, "qa_gate_lower", 0.3), \
             patch.object(config, "qa_gate_upper", 0.7):
            lower, upper = t.get_gate_bounds(category="shell_commands")
            assert lower == pytest.approx(0.4)
            assert upper == pytest.approx(0.6)

    def test_overall_quality_score_empty(self):
        t = self._make_trust()
        qs = t.overall_quality_score()
        assert qs["score"] is None
        assert qs["total_validated"] == 0

    def test_overall_quality_score_with_data(self):
        t = self._make_trust()
        for _ in range(50):
            t.record_outcome("git_commands", passed=True, score=5)
        for _ in range(50):
            t.record_outcome("code_gen", passed=True, score=5)
        t.record_outcome("code_gen", passed=False, score=2)
        qs = t.overall_quality_score()
        assert qs["score"] is not None
        assert qs["total_validated"] == 101
        assert qs["categories_count"] == 2

    def test_cost_summary(self):
        t = self._make_trust()
        t.record_outcome("test", passed=True, shadow_tokens=1000)
        cs = t.cost_summary()
        assert cs["total_gated"] == 1
        assert cs["shadow_tokens_used"] == 1000
        assert cs["estimated_shadow_cost_usd"] > 0

    def test_status(self):
        t = self._make_trust()
        s = t.status()
        assert "enabled" in s
        assert "quality" in s
        assert "cost" in s
        assert "gate_bounds" in s
        assert "categories_summary" in s

    def test_reset(self):
        t = self._make_trust()
        t.record_outcome("test", passed=True)
        t.record_bypass()
        t.reset()
        assert len(t._categories) == 0
        assert t._total_gated == 0
        assert t._total_bypassed == 0

    def test_get_all_categories(self):
        t = self._make_trust()
        t.record_outcome("a", passed=True)
        t.record_outcome("b", passed=True)
        t.record_outcome("b", passed=True)
        cats = t.get_all_categories()
        assert len(cats) == 2
        # Sorted by total_samples desc
        assert cats[0]["category"] == "b"
