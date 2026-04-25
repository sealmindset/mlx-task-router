from __future__ import annotations

import os
from dataclasses import dataclass, field
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


@dataclass
class GearProfile:
    name: str
    model: str
    description: str
    max_tokens: int = 8192


DEFAULT_GEARS: dict[str, GearProfile] = {
    "eco": GearProfile(
        name="eco",
        model="mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
        description="Fast and light — simple code tasks",
        max_tokens=4096,
    ),
    "sport": GearProfile(
        name="sport",
        model="mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit",
        description="Balanced — default for CLI tasks",
        max_tokens=8192,
    ),
    "track": GearProfile(
        name="track",
        model="mlx-community/Qwen3-Coder-Next-4bit",
        description="Maximum capability — complex local tasks",
        max_tokens=16384,
    ),
}


def _build_gears() -> dict[str, GearProfile]:
    gears = {}
    for name, default in DEFAULT_GEARS.items():
        env_key = f"GEAR_{name.upper()}_MODEL"
        model_override = os.getenv(env_key)
        gears[name] = GearProfile(
            name=default.name,
            model=model_override or default.model,
            description=default.description,
            max_tokens=default.max_tokens,
        )
    return gears


@dataclass
class Config:
    host: str = ""
    port: int = 0
    default_gear: str = ""
    anthropic_api_key: str = ""
    anthropic_api_url: str = ""
    max_local_context_tokens: int = 0
    routing_threshold: float = 0.0
    log_routing: bool = True
    gears: dict[str, GearProfile] = field(default_factory=dict)

    def __post_init__(self):
        _load_dotenv_once()
        self.host = self.host or os.getenv("HOST", "0.0.0.0")
        self.port = self.port or int(os.getenv("PORT", "8888"))
        self.default_gear = self.default_gear or os.getenv("DEFAULT_GEAR", "sport")
        self.anthropic_api_key = self.anthropic_api_key or os.getenv(
            "ANTHROPIC_API_KEY", ""
        )
        self.anthropic_api_url = self.anthropic_api_url or os.getenv(
            "ANTHROPIC_API_URL", "https://api.anthropic.com"
        )
        self.max_local_context_tokens = self.max_local_context_tokens or int(
            os.getenv("MAX_LOCAL_CONTEXT_TOKENS", "32000")
        )
        self.routing_threshold = self.routing_threshold or float(
            os.getenv("ROUTING_THRESHOLD", "0.5")
        )
        self.log_routing = os.getenv("LOG_ROUTING", "true").lower() == "true"
        if not self.gears:
            self.gears = _build_gears()


config = Config()
