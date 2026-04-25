from __future__ import annotations

from mlx_task_router.stats import Stats, _detect_tier


class TestDetectTier:
    # Claude 4 family
    def test_opus_4(self):
        assert _detect_tier("claude-opus-4-20250514") == "opus_4"

    def test_sonnet_4(self):
        assert _detect_tier("claude-sonnet-4-20250514") == "sonnet_4"

    # Claude 3.5 family
    def test_sonnet_3_5(self):
        assert _detect_tier("claude-3-5-sonnet-20241022") == "sonnet_3_5"

    def test_haiku_3_5(self):
        assert _detect_tier("claude-3-5-haiku-20241022") == "haiku_3_5"

    # Claude 3 family
    def test_opus_3(self):
        assert _detect_tier("claude-3-opus-20240229") == "opus_3"

    def test_sonnet_3(self):
        assert _detect_tier("claude-3-sonnet-20240229") == "sonnet_3"

    def test_haiku_3(self):
        assert _detect_tier("claude-3-haiku-20240307") == "haiku_3"

    # Fallback
    def test_unknown_defaults_sonnet_4(self):
        assert _detect_tier("some-random-model") == "sonnet_4"

    def test_case_insensitive(self):
        assert _detect_tier("Claude-OPUS-4-20250514") == "opus_4"

    def test_generic_opus_fallback(self):
        assert _detect_tier("my-opus-model") == "opus_4"

    def test_generic_haiku_fallback(self):
        assert _detect_tier("my-haiku-model") == "haiku_3_5"


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
