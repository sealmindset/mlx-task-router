from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "mlx-task-router"

_dotenv_loaded = False


def _load_dotenv_once():
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    from dotenv import load_dotenv

    config_env = CONFIG_DIR / ".env"
    if config_env.exists():
        load_dotenv(config_env)
    load_dotenv()


DEFAULT_MODEL = "mlx-community/Qwen3.6-27B-OptiQ-4bit"
DEFAULT_MAX_TOKENS = 16384


@dataclass
class Config:
    host: str = ""
    port: int = 0
    model_name: str = ""
    model_max_tokens: int = 0
    temperature: float = -1.0
    top_p: float = -1.0
    top_k: int = -1
    repetition_penalty: float = -1.0
    draft_model: str = ""
    speculative_tokens: int = 0
    anthropic_api_key: str = ""
    anthropic_api_url: str = ""
    max_local_context_tokens: int = 0
    routing_threshold: float = 0.0
    adaptive_routing: bool = True
    log_routing: bool = True
    embed_routing: bool = True
    embed_weight: float = 0.0
    embed_min_samples: int = 0
    fast_model: str = ""
    fast_model_max_tokens: int = 0
    trivial_threshold: float = 0.0
    verify_enabled: bool = False
    verify_sample_rate: float = 0.0
    verify_shadow_mode: bool = False
    verify_model: str = ""
    verify_queue_size: int = 0
    verify_auto_tune: bool = True
    verify_min_score: int = 0
    verify_alert_webhook: str = ""
    qa_gate_enabled: bool = False
    qa_gate_lower: float = 0.0
    qa_gate_upper: float = 0.0
    qa_gate_timeout: int = 0
    qa_gate_validation_model: str = ""
    qa_trust_min_samples: int = 0
    qa_trust_proven_samples: int = 0
    qa_trust_pass_threshold: float = 0.0
    qa_trust_proven_threshold: float = 0.0

    def __post_init__(self):
        _load_dotenv_once()
        self.host = self.host or os.getenv("HOST", "0.0.0.0")
        self.port = self.port or int(os.getenv("PORT", "8888"))
        self.model_name = self.model_name or os.getenv("MLX_MODEL", DEFAULT_MODEL)
        self.model_max_tokens = self.model_max_tokens or int(
            os.getenv("MLX_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))
        )
        if self.temperature < 0:
            self.temperature = float(os.getenv("MLX_TEMPERATURE", "0.6"))
        if self.top_p < 0:
            self.top_p = float(os.getenv("MLX_TOP_P", "0.95"))
        if self.top_k < 0:
            self.top_k = int(os.getenv("MLX_TOP_K", "20"))
        if self.repetition_penalty < 0:
            self.repetition_penalty = float(os.getenv("MLX_REPETITION_PENALTY", "1.05"))
        self.draft_model = self.draft_model or os.getenv("MLX_DRAFT_MODEL", "")
        self.speculative_tokens = self.speculative_tokens or int(
            os.getenv("MLX_SPECULATIVE_TOKENS", "5")
        )
        self.anthropic_api_key = self.anthropic_api_key or os.getenv(
            "ANTHROPIC_API_KEY", ""
        )
        self.anthropic_api_url = self.anthropic_api_url or os.getenv(
            "ANTHROPIC_API_URL", "https://api.anthropic.com"
        )
        self.max_local_context_tokens = self.max_local_context_tokens or int(
            os.getenv("MAX_LOCAL_CONTEXT_TOKENS", "65536")
        )
        self.routing_threshold = self.routing_threshold or float(
            os.getenv("ROUTING_THRESHOLD", "0.7")
        )
        self.adaptive_routing = os.getenv("ADAPTIVE_ROUTING", "true").lower() == "true"
        self.log_routing = os.getenv("LOG_ROUTING", "true").lower() == "true"
        self.embed_routing = os.getenv("EMBED_ROUTING", "true").lower() == "true"
        if self.embed_weight == 0.0:
            self.embed_weight = float(os.getenv("EMBED_WEIGHT", "0.3"))
        if self.embed_min_samples == 0:
            self.embed_min_samples = int(os.getenv("EMBED_MIN_SAMPLES", "100"))
        self.fast_model = self.fast_model or os.getenv("MLX_FAST_MODEL", "")
        if self.fast_model_max_tokens == 0:
            self.fast_model_max_tokens = int(os.getenv("FAST_MODEL_MAX_TOKENS", "2048"))
        if self.trivial_threshold == 0.0:
            self.trivial_threshold = float(os.getenv("TRIVIAL_THRESHOLD", "0.3"))
        self.verify_enabled = os.getenv("VERIFY_ENABLED", "false").lower() == "true"
        if self.verify_sample_rate == 0.0:
            self.verify_sample_rate = float(os.getenv("VERIFY_SAMPLE_RATE", "0.0"))
        self.verify_shadow_mode = os.getenv("VERIFY_SHADOW_MODE", "false").lower() == "true"
        self.verify_model = self.verify_model or os.getenv(
            "VERIFY_MODEL", "claude-sonnet-4-20250514"
        )
        if self.verify_queue_size == 0:
            self.verify_queue_size = int(os.getenv("VERIFY_QUEUE_SIZE", "50"))
        self.verify_auto_tune = os.getenv("VERIFY_AUTO_TUNE", "true").lower() == "true"
        if self.verify_min_score == 0:
            self.verify_min_score = int(os.getenv("VERIFY_MIN_SCORE", "3"))
        self.verify_alert_webhook = self.verify_alert_webhook or os.getenv(
            "VERIFY_ALERT_WEBHOOK", ""
        )
        self.qa_gate_enabled = os.getenv("QA_GATE_ENABLED", "false").lower() == "true"
        if self.qa_gate_lower == 0.0:
            self.qa_gate_lower = float(os.getenv("QA_GATE_LOWER", "0.3"))
        if self.qa_gate_upper == 0.0:
            self.qa_gate_upper = float(os.getenv("QA_GATE_UPPER", "0.7"))
        if self.qa_gate_timeout == 0:
            self.qa_gate_timeout = int(os.getenv("QA_GATE_TIMEOUT", "10"))
        self.qa_gate_validation_model = self.qa_gate_validation_model or os.getenv(
            "QA_GATE_VALIDATION_MODEL", "claude-sonnet-4-20250514"
        )
        if self.qa_trust_min_samples == 0:
            self.qa_trust_min_samples = int(os.getenv("QA_TRUST_MIN_SAMPLES", "50"))
        if self.qa_trust_proven_samples == 0:
            self.qa_trust_proven_samples = int(os.getenv("QA_TRUST_PROVEN_SAMPLES", "100"))
        if self.qa_trust_pass_threshold == 0.0:
            self.qa_trust_pass_threshold = float(os.getenv("QA_TRUST_PASS_THRESHOLD", "0.95"))
        if self.qa_trust_proven_threshold == 0.0:
            self.qa_trust_proven_threshold = float(os.getenv("QA_TRUST_PROVEN_THRESHOLD", "0.98"))

    def reload(self) -> dict[str, str]:
        """Re-read .env and update all settings. Returns dict of changed fields."""
        global _dotenv_loaded
        _dotenv_loaded = False
        _load_dotenv_once()

        old_values = {
            "model_name": self.model_name,
            "model_max_tokens": self.model_max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "repetition_penalty": self.repetition_penalty,
            "routing_threshold": self.routing_threshold,
            "adaptive_routing": self.adaptive_routing,
            "log_routing": self.log_routing,
            "max_local_context_tokens": self.max_local_context_tokens,
            "embed_routing": self.embed_routing,
            "embed_weight": self.embed_weight,
            "fast_model": self.fast_model,
            "trivial_threshold": self.trivial_threshold,
            "verify_enabled": self.verify_enabled,
            "verify_shadow_mode": self.verify_shadow_mode,
            "verify_sample_rate": self.verify_sample_rate,
            "verify_auto_tune": self.verify_auto_tune,
            "qa_gate_enabled": self.qa_gate_enabled,
            "qa_gate_lower": self.qa_gate_lower,
            "qa_gate_upper": self.qa_gate_upper,
        }

        self.model_name = os.getenv("MLX_MODEL", DEFAULT_MODEL)
        self.model_max_tokens = int(os.getenv("MLX_MAX_TOKENS", str(DEFAULT_MAX_TOKENS)))
        self.temperature = float(os.getenv("MLX_TEMPERATURE", "0.6"))
        self.top_p = float(os.getenv("MLX_TOP_P", "0.95"))
        self.top_k = int(os.getenv("MLX_TOP_K", "20"))
        self.repetition_penalty = float(os.getenv("MLX_REPETITION_PENALTY", "1.05"))
        self.routing_threshold = float(os.getenv("ROUTING_THRESHOLD", "0.7"))
        self.adaptive_routing = os.getenv("ADAPTIVE_ROUTING", "true").lower() == "true"
        self.log_routing = os.getenv("LOG_ROUTING", "true").lower() == "true"
        self.max_local_context_tokens = int(os.getenv("MAX_LOCAL_CONTEXT_TOKENS", "65536"))
        self.embed_routing = os.getenv("EMBED_ROUTING", "true").lower() == "true"
        self.embed_weight = float(os.getenv("EMBED_WEIGHT", "0.3"))
        self.fast_model = os.getenv("MLX_FAST_MODEL", "")
        self.trivial_threshold = float(os.getenv("TRIVIAL_THRESHOLD", "0.3"))
        self.verify_enabled = os.getenv("VERIFY_ENABLED", "false").lower() == "true"
        self.verify_shadow_mode = os.getenv("VERIFY_SHADOW_MODE", "false").lower() == "true"
        self.verify_sample_rate = float(os.getenv("VERIFY_SAMPLE_RATE", "0.0"))
        self.verify_auto_tune = os.getenv("VERIFY_AUTO_TUNE", "true").lower() == "true"
        self.qa_gate_enabled = os.getenv("QA_GATE_ENABLED", "false").lower() == "true"
        self.qa_gate_lower = float(os.getenv("QA_GATE_LOWER", "0.3"))
        self.qa_gate_upper = float(os.getenv("QA_GATE_UPPER", "0.7"))

        changes = {}
        new_values = {
            "model_name": self.model_name,
            "model_max_tokens": self.model_max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "repetition_penalty": self.repetition_penalty,
            "routing_threshold": self.routing_threshold,
            "adaptive_routing": self.adaptive_routing,
            "log_routing": self.log_routing,
            "max_local_context_tokens": self.max_local_context_tokens,
            "embed_routing": self.embed_routing,
            "embed_weight": self.embed_weight,
            "fast_model": self.fast_model,
            "trivial_threshold": self.trivial_threshold,
            "verify_enabled": self.verify_enabled,
            "verify_shadow_mode": self.verify_shadow_mode,
            "verify_sample_rate": self.verify_sample_rate,
            "verify_auto_tune": self.verify_auto_tune,
            "qa_gate_enabled": self.qa_gate_enabled,
            "qa_gate_lower": self.qa_gate_lower,
            "qa_gate_upper": self.qa_gate_upper,
        }
        for key in old_values:
            if old_values[key] != new_values[key]:
                changes[key] = f"{old_values[key]} → {new_values[key]}"

        return changes


config = Config()
