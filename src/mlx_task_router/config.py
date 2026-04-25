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


DEFAULT_MODEL = "mlx-community/Qwen3-Coder-Next-4bit"
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

    def __post_init__(self):
        _load_dotenv_once()
        self.host = self.host or os.getenv("HOST", "0.0.0.0")
        self.port = self.port or int(os.getenv("PORT", "8888"))
        self.model_name = self.model_name or os.getenv("MLX_MODEL", DEFAULT_MODEL)
        self.model_max_tokens = self.model_max_tokens or int(
            os.getenv("MLX_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))
        )
        if self.temperature < 0:
            self.temperature = float(os.getenv("MLX_TEMPERATURE", "1.0"))
        if self.top_p < 0:
            self.top_p = float(os.getenv("MLX_TOP_P", "0.95"))
        if self.top_k < 0:
            self.top_k = int(os.getenv("MLX_TOP_K", "40"))
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
            os.getenv("ROUTING_THRESHOLD", "0.5")
        )
        self.adaptive_routing = os.getenv("ADAPTIVE_ROUTING", "true").lower() == "true"
        self.log_routing = os.getenv("LOG_ROUTING", "true").lower() == "true"

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
        }

        self.model_name = os.getenv("MLX_MODEL", DEFAULT_MODEL)
        self.model_max_tokens = int(os.getenv("MLX_MAX_TOKENS", str(DEFAULT_MAX_TOKENS)))
        self.temperature = float(os.getenv("MLX_TEMPERATURE", "1.0"))
        self.top_p = float(os.getenv("MLX_TOP_P", "0.95"))
        self.top_k = int(os.getenv("MLX_TOP_K", "40"))
        self.repetition_penalty = float(os.getenv("MLX_REPETITION_PENALTY", "1.05"))
        self.routing_threshold = float(os.getenv("ROUTING_THRESHOLD", "0.5"))
        self.adaptive_routing = os.getenv("ADAPTIVE_ROUTING", "true").lower() == "true"
        self.log_routing = os.getenv("LOG_ROUTING", "true").lower() == "true"
        self.max_local_context_tokens = int(os.getenv("MAX_LOCAL_CONTEXT_TOKENS", "65536"))

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
        }
        for key in old_values:
            if old_values[key] != new_values[key]:
                changes[key] = f"{old_values[key]} → {new_values[key]}"

        return changes


config = Config()
