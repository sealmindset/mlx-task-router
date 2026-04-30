"""Tests for adaptive forward threshold.

With aggressive routing (default LOCAL), the adaptive threshold controls
the forward_score needed to FORWARD. Higher threshold = more stays local.
Low failure rate → RAISE threshold (keep more local).
High failure rate → LOWER threshold (forward more).
"""

from __future__ import annotations

from unittest.mock import patch

from mlx_task_router.router import _adaptive_threshold, FORWARD_THRESHOLD


class TestAdaptiveThreshold:
    def test_returns_base_when_disabled(self):
        with patch("mlx_task_router.router.config") as mock_cfg:
            mock_cfg.adaptive_routing = False
            mock_cfg.routing_threshold = FORWARD_THRESHOLD
            assert _adaptive_threshold() == FORWARD_THRESHOLD

    def test_returns_base_when_no_feedback(self):
        with patch("mlx_task_router.router.routing_feedback") as mock_fb:
            mock_fb.stats.return_value = {}
            assert _adaptive_threshold() == FORWARD_THRESHOLD

    def test_returns_base_when_insufficient_data(self):
        with patch("mlx_task_router.router.routing_feedback") as mock_fb:
            mock_fb.stats.return_value = {
                "exec:git": {"attempts": 5, "failures": 0, "failure_rate": "0%", "penalty": 0},
            }
            assert _adaptive_threshold() == FORWARD_THRESHOLD

    def test_raises_threshold_on_low_failure_rate(self):
        """Low failure = local is working well → raise forward threshold (keep more local)."""
        with patch("mlx_task_router.router.routing_feedback") as mock_fb:
            mock_fb.stats.return_value = {
                "exec:git": {"attempts": 30, "failures": 1, "failure_rate": "3%", "penalty": 0},
            }
            threshold = _adaptive_threshold()
            assert threshold > FORWARD_THRESHOLD

    def test_lowers_threshold_on_high_failure_rate(self):
        """High failure = local is struggling → lower forward threshold (forward more)."""
        with patch("mlx_task_router.router.routing_feedback") as mock_fb:
            mock_fb.stats.return_value = {
                "exec:git": {"attempts": 15, "failures": 5, "failure_rate": "33%", "penalty": -0.13},
                "exec:npm": {"attempts": 10, "failures": 5, "failure_rate": "50%", "penalty": -0.2},
            }
            threshold = _adaptive_threshold()
            assert threshold < FORWARD_THRESHOLD

    def test_threshold_clamped_low(self):
        with patch("mlx_task_router.router.routing_feedback") as mock_fb:
            mock_fb.stats.return_value = {
                "exec:git": {"attempts": 100, "failures": 90, "failure_rate": "90%", "penalty": -0.36},
            }
            threshold = _adaptive_threshold()
            assert threshold >= 0.2

    def test_threshold_clamped_high(self):
        with patch("mlx_task_router.router.routing_feedback") as mock_fb:
            mock_fb.stats.return_value = {
                "exec:git": {"attempts": 100, "failures": 0, "failure_rate": "0%", "penalty": 0},
            }
            threshold = _adaptive_threshold()
            assert threshold <= 0.8
