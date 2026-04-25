from __future__ import annotations

from mlx_task_router.stats import Stats, _detect_tier


class TestDetectTier:
    def test_opus(self):
        assert _detect_tier("claude-opus-4") == "opus"

    def test_haiku(self):
        assert _detect_tier("claude-haiku-3") == "haiku"

    def test_sonnet_default(self):
        assert _detect_tier("claude-sonnet-4") == "sonnet"

    def test_unknown_defaults_sonnet(self):
        assert _detect_tier("some-random-model") == "sonnet"

    def test_case_insensitive(self):
        assert _detect_tier("Claude-OPUS-4") == "opus"


class TestStats:
    def test_record_local(self):
        s = Stats()
        s.record_local(100, 50, "claude-sonnet-4")
        data = s.get()
        assert data["requests_total"] == 1
        assert data["requests_local"] == 1
        assert data["tokens_local_input"] == 100
        assert data["tokens_local_output"] == 50
        assert data["cost_saved_usd"] > 0

    def test_record_forward(self):
        s = Stats()
        s.record_forward(200, 100)
        data = s.get()
        assert data["requests_total"] == 1
        assert data["requests_forwarded"] == 1
        assert data["tokens_forwarded_input"] == 200

    def test_local_percentage(self):
        s = Stats()
        s.record_local(10, 10)
        s.record_forward(10, 10)
        data = s.get()
        assert data["local_percentage"] == 50.0

    def test_reset(self):
        s = Stats()
        s.record_local(100, 50)
        s.reset()
        data = s.get()
        assert data["requests_total"] == 0
        assert data["cost_saved_usd"] == 0.0

    def test_cost_calculation_sonnet(self):
        s = Stats()
        s.record_local(1_000_000, 0, "claude-sonnet-4")
        data = s.get()
        assert data["cost_saved_usd"] == 3.0

    def test_cost_display_format(self):
        s = Stats()
        s.record_local(100, 50)
        data = s.get()
        assert data["cost_saved_display"].startswith("$")
