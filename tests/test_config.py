from __future__ import annotations

from mlx_task_router.config import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, Config


class TestConfigDefaults:
    def test_default_model(self):
        assert DEFAULT_MODEL == "mlx-community/Qwen3.6-27B-OptiQ-4bit"

    def test_default_max_tokens(self):
        assert DEFAULT_MAX_TOKENS == 16384

    def test_config_uses_defaults(self):
        c = Config()
        assert c.model_name == DEFAULT_MODEL
        assert c.model_max_tokens == DEFAULT_MAX_TOKENS

    def test_generation_defaults(self, monkeypatch):
        monkeypatch.delenv("MLX_DRAFT_MODEL", raising=False)
        c = Config()
        assert c.temperature == 0.6
        assert c.top_p == 0.95
        assert c.top_k == 20
        assert c.repetition_penalty == 1.05
        assert c.draft_model == ""
        assert c.speculative_tokens == 5
        assert c.adaptive_routing is True


class TestConfigEnvOverride:
    def test_model_override(self, monkeypatch):
        monkeypatch.setenv("MLX_MODEL", "custom/model")
        c = Config()
        assert c.model_name == "custom/model"

    def test_max_tokens_override(self, monkeypatch):
        monkeypatch.setenv("MLX_MAX_TOKENS", "4096")
        c = Config()
        assert c.model_max_tokens == 4096

    def test_temperature_override(self, monkeypatch):
        monkeypatch.setenv("MLX_TEMPERATURE", "0.7")
        c = Config()
        assert c.temperature == 0.7

    def test_draft_model_override(self, monkeypatch):
        monkeypatch.setenv("MLX_DRAFT_MODEL", "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit")
        c = Config()
        assert c.draft_model == "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"

    def test_adaptive_routing_disabled(self, monkeypatch):
        monkeypatch.setenv("ADAPTIVE_ROUTING", "false")
        c = Config()
        assert c.adaptive_routing is False

    def test_embed_routing_defaults(self, monkeypatch):
        monkeypatch.delenv("EMBED_ROUTING", raising=False)
        monkeypatch.delenv("EMBED_WEIGHT", raising=False)
        monkeypatch.delenv("EMBED_MIN_SAMPLES", raising=False)
        c = Config()
        assert c.embed_routing is True
        assert c.embed_weight == 0.3
        assert c.embed_min_samples == 100

    def test_embed_routing_disabled(self, monkeypatch):
        monkeypatch.setenv("EMBED_ROUTING", "false")
        c = Config()
        assert c.embed_routing is False

    def test_fast_model_defaults(self, monkeypatch):
        monkeypatch.delenv("MLX_FAST_MODEL", raising=False)
        monkeypatch.delenv("FAST_MODEL_MAX_TOKENS", raising=False)
        monkeypatch.delenv("TRIVIAL_THRESHOLD", raising=False)
        c = Config()
        assert c.fast_model == ""
        assert c.fast_model_max_tokens == 2048
        assert c.trivial_threshold == 0.3

    def test_fast_model_override(self, monkeypatch):
        monkeypatch.setenv("MLX_FAST_MODEL", "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit")
        c = Config()
        assert c.fast_model == "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"
