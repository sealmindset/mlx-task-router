"""Tests for self-annealing routing weights."""

from __future__ import annotations

from mlx_task_router.annealing import WeightAnnealer


class TestWeightAnnealer:
    def test_no_adjustment_by_default(self):
        wa = WeightAnnealer()
        wa._adjustments = {}
        assert wa.get_adjustment("complex") == 0.0
        assert wa.get_adjustment("exec") == 0.0

    def test_get_adjustment_returns_stored_value(self):
        wa = WeightAnnealer()
        wa._adjustments = {"complex": 0.15, "exec": -0.1}
        assert wa.get_adjustment("complex") == 0.15
        assert wa.get_adjustment("exec") == -0.1
        assert wa.get_adjustment("unknown") == 0.0

    def test_status_output(self):
        wa = WeightAnnealer()
        wa._adjustments = {"complex": 0.05}
        s = wa.status()
        assert "adjustments" in s
        assert s["adjustments"]["complex"] == 0.05
        assert "learning_rate" in s
        assert "min_samples" in s

    def test_reset_clears_adjustments(self):
        wa = WeightAnnealer()
        wa._adjustments = {"complex": 0.1, "exec": -0.05}
        wa._history = [{"ts": 1, "adj": {}}]
        wa.reset()
        assert wa._adjustments == {}
        assert wa._history == []

    def test_anneal_step_skips_low_samples(self):
        """Anneal step should do nothing when total attempts < min_samples."""
        wa = WeightAnnealer()
        wa._adjustments = {}
        # Mock feedback with insufficient data
        import mlx_task_router.annealing as ann
        original_min = ann._MIN_SAMPLES
        ann._MIN_SAMPLES = 100
        try:
            wa._anneal_step()
            assert wa._adjustments == {}
        finally:
            ann._MIN_SAMPLES = original_min
