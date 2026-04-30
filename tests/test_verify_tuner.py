"""Tests for verify_tuner.py — auto-tuning from TBV results."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from mlx_task_router.verify import VerificationResult
from mlx_task_router.verify_tuner import VerifyTuner, _DECAY_HALF_LIFE, _MAX_ADJUSTMENT, _STEP_SIZE


@pytest.fixture
def tuner():
    """Fresh tuner for each test."""
    t = VerifyTuner()
    t.reset()
    return t


def _make_result(
    route="local",
    scores=None,
    overall_pass=True,
    could_be_local=True,
    suggested_route="local",
    confidence=0.9,
    strategy="local_check",
    error="",
):
    if scores is None:
        scores = {"correctness": 5, "completeness": 5, "code_quality": 5, "routing_appropriateness": 5}
    return VerificationResult(
        timestamp=time.time(),
        request_hash="test123",
        route=route,
        mode="async",
        strategy=strategy,
        scores=scores,
        overall_pass=overall_pass,
        could_be_local=could_be_local,
        suggested_route=suggested_route,
        confidence=confidence,
        error=error,
    )


class TestProcessResult:
    """Tests for result processing and action determination."""

    def test_skips_when_disabled(self, tuner, monkeypatch):
        """Does nothing when auto_tune is disabled."""
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_auto_tune", False)
        result = _make_result(scores={"correctness": 1, "completeness": 1, "code_quality": 1, "routing_appropriateness": 1})
        tuner.process_result(result)
        assert tuner.get_adjustment("complexity") == 0.0

    def test_skips_errors(self, tuner, monkeypatch):
        """Skips results with errors."""
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_auto_tune", True)
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_min_score", 3)
        result = _make_result(error="Connection failed")
        tuner.process_result(result)
        assert tuner._total_processed == 0

    def test_skips_low_confidence(self, tuner, monkeypatch):
        """Skips results with low confidence."""
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_auto_tune", True)
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_min_score", 3)
        result = _make_result(confidence=0.3)
        tuner.process_result(result)
        assert tuner._total_processed == 0

    def test_increase_forward_on_local_failure(self, tuner, monkeypatch):
        """Local failure increases forward signal weights."""
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_auto_tune", True)
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_min_score", 3)
        result = _make_result(
            route="local",
            scores={"correctness": 2, "completeness": 3, "code_quality": 3, "routing_appropriateness": 3},
            overall_pass=False,
        )
        tuner.process_result(result)
        assert tuner.get_adjustment("complexity") > 0

    def test_decrease_forward_on_perfect_local(self, tuner, monkeypatch):
        """Perfect local response decreases forward signal weights."""
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_auto_tune", True)
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_min_score", 3)
        result = _make_result(
            route="local",
            scores={"correctness": 5, "completeness": 5, "code_quality": 5, "routing_appropriateness": 5},
            overall_pass=True,
        )
        tuner.process_result(result)
        assert tuner.get_adjustment("complexity") < 0

    def test_lower_threshold_on_missed_local(self, tuner, monkeypatch):
        """Missed local opportunity lowers threshold."""
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_auto_tune", True)
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_min_score", 3)
        result = _make_result(
            route="forward",
            strategy="retroactive",
            could_be_local=True,
            confidence=0.9,
            suggested_route="local",
        )
        tuner.process_result(result)
        assert tuner.get_threshold_adjustment() < 0

    def test_raise_threshold_on_correct_forward(self, tuner, monkeypatch):
        """Correctly forwarded request slightly raises threshold (confirmation)."""
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_auto_tune", True)
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_min_score", 3)
        result = _make_result(
            route="forward",
            strategy="retroactive",
            could_be_local=False,
            suggested_route="forward",
        )
        tuner.process_result(result)
        assert tuner.get_threshold_adjustment() > 0

    def test_reduce_trivial_on_fast_failure(self, tuner, monkeypatch):
        """Fast model failure reduces trivial sensitivity."""
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_auto_tune", True)
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_min_score", 3)
        result = _make_result(
            route="fast",
            scores={"correctness": 2, "completeness": 2, "code_quality": 3, "routing_appropriateness": 3},
            overall_pass=False,
            suggested_route="local",
        )
        tuner.process_result(result)
        assert tuner.get_adjustment("trivial_sensitivity") > 0


class TestBounds:
    """Tests for adjustment bounds."""

    def test_max_positive_bound(self, tuner, monkeypatch):
        """Adjustments cannot exceed +MAX_ADJUSTMENT."""
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_auto_tune", True)
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_min_score", 3)
        # Force many failures to push adjustment high
        for _ in range(100):
            result = _make_result(
                route="local",
                scores={"correctness": 1, "completeness": 1, "code_quality": 1, "routing_appropriateness": 1},
                overall_pass=False,
                confidence=1.0,
            )
            tuner.process_result(result)
        assert tuner.get_adjustment("complexity") <= _MAX_ADJUSTMENT

    def test_max_negative_bound(self, tuner, monkeypatch):
        """Adjustments cannot go below -MAX_ADJUSTMENT."""
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_auto_tune", True)
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_min_score", 3)
        for _ in range(100):
            result = _make_result(
                route="local",
                scores={"correctness": 5, "completeness": 5, "code_quality": 5, "routing_appropriateness": 5},
                overall_pass=True,
                confidence=1.0,
            )
            tuner.process_result(result)
        assert tuner.get_adjustment("complexity") >= -_MAX_ADJUSTMENT


class TestDecay:
    """Tests for exponential decay."""

    def test_decay_reduces_adjustments(self, tuner, monkeypatch):
        """Decay reduces adjustment magnitudes over time."""
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_auto_tune", True)
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_min_score", 3)
        # Set a manual adjustment
        tuner._adjustments["complexity"] = 0.1
        tuner._total_processed = 0
        tuner._last_decay_at = 0

        # Simulate processing many results that don't trigger actions
        tuner._total_processed = 200
        tuner._maybe_decay()

        # After 200 steps (one half-life), should be ~0.05
        assert tuner._adjustments["complexity"] < 0.1
        assert tuner._adjustments["complexity"] > 0.0

    def test_no_decay_below_threshold(self, tuner):
        """No decay applied if fewer than 10 steps since last decay."""
        tuner._adjustments["complexity"] = 0.1
        tuner._total_processed = 5
        tuner._last_decay_at = 0
        tuner._maybe_decay()
        assert tuner._adjustments["complexity"] == 0.1


class TestReset:
    """Tests for reset functionality."""

    def test_reset_clears_everything(self, tuner, monkeypatch):
        """Reset clears all adjustments and state."""
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_auto_tune", True)
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_min_score", 3)
        tuner._adjustments["complexity"] = 0.1
        tuner._total_processed = 50
        tuner._learned_patterns = [{"pattern": "test", "strength": 0.5}]
        tuner.reset()
        assert tuner._adjustments["complexity"] == 0.0
        assert tuner._total_processed == 0
        assert len(tuner._learned_patterns) == 0


class TestStatus:
    """Tests for status reporting."""

    def test_status_empty(self, tuner, monkeypatch):
        """Status with no adjustments."""
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_auto_tune", True)
        s = tuner.status()
        assert s["enabled"] is True
        assert s["total_processed"] == 0
        assert s["adjustments"] == {}

    def test_status_with_adjustments(self, tuner, monkeypatch):
        """Status includes non-zero adjustments."""
        monkeypatch.setattr("mlx_task_router.verify_tuner.config.verify_auto_tune", True)
        tuner._adjustments["complexity"] = 0.05
        tuner._adjustments["threshold"] = -0.02
        tuner._total_processed = 10
        s = tuner.status()
        assert s["total_processed"] == 10
        assert "complexity" in s["adjustments"]
        assert "threshold" in s["adjustments"]


class TestGetAdjustment:
    """Tests for individual adjustment retrieval."""

    def test_get_existing_adjustment(self, tuner):
        """Returns the adjustment value for a known signal."""
        tuner._adjustments["complexity"] = 0.05
        assert tuner.get_adjustment("complexity") == 0.05

    def test_get_unknown_signal(self, tuner):
        """Returns 0 for unknown signals."""
        assert tuner.get_adjustment("nonexistent") == 0.0

    def test_get_all_adjustments(self, tuner):
        """Returns all adjustments as a dict copy."""
        tuner._adjustments["complexity"] = 0.05
        all_adj = tuner.get_all_adjustments()
        assert all_adj["complexity"] == 0.05
        # Verify it's a copy
        all_adj["complexity"] = 999
        assert tuner._adjustments["complexity"] == 0.05
