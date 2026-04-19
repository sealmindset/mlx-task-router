"""Request statistics and cost tracking with periodic disk persistence."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from mlx_task_router.config import CONFIG_DIR

STATS_FILE = CONFIG_DIR / "stats.json"

# Anthropic pricing per million tokens (as of 2025)
# Using Sonnet as the baseline — most common Claude Code model
PRICING = {
    "sonnet": {"input": 3.00, "output": 15.00},
    "opus": {"input": 15.00, "output": 75.00},
    "haiku": {"input": 0.25, "output": 1.25},
}
DEFAULT_TIER = "sonnet"

_FLUSH_INTERVAL = 30  # seconds


class Stats:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = self._load()
        self._dirty = False
        self._flush_thread: threading.Thread | None = None
        self._running = False

    def _load(self) -> dict[str, Any]:
        defaults = {
            "requests_total": 0,
            "requests_local": 0,
            "requests_forwarded": 0,
            "tokens_local_input": 0,
            "tokens_local_output": 0,
            "tokens_forwarded_input": 0,
            "tokens_forwarded_output": 0,
            "cost_saved_usd": 0.0,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "last_reset": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if STATS_FILE.exists():
            try:
                saved = json.loads(STATS_FILE.read_text())
                defaults.update(saved)
            except (json.JSONDecodeError, OSError):
                pass
        return defaults

    def _flush(self):
        with self._lock:
            if not self._dirty:
                return
            try:
                STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
                STATS_FILE.write_text(json.dumps(self._data, indent=2))
                self._dirty = False
            except OSError:
                pass

    def _flush_loop(self):
        while self._running:
            time.sleep(_FLUSH_INTERVAL)
            self._flush()

    def start(self):
        self._running = True
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

    def stop(self):
        self._running = False
        self._flush()

    def record_local(self, input_tokens: int, output_tokens: int, model: str = ""):
        tier = _detect_tier(model)
        pricing = PRICING[tier]
        saved = (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000

        with self._lock:
            self._data["requests_total"] += 1
            self._data["requests_local"] += 1
            self._data["tokens_local_input"] += input_tokens
            self._data["tokens_local_output"] += output_tokens
            self._data["cost_saved_usd"] = round(self._data["cost_saved_usd"] + saved, 6)
            self._dirty = True

    def record_forward(self, input_tokens: int = 0, output_tokens: int = 0):
        with self._lock:
            self._data["requests_total"] += 1
            self._data["requests_forwarded"] += 1
            self._data["tokens_forwarded_input"] += input_tokens
            self._data["tokens_forwarded_output"] += output_tokens
            self._dirty = True

    def get(self) -> dict[str, Any]:
        with self._lock:
            snapshot = dict(self._data)

        local_pct = 0.0
        if snapshot["requests_total"] > 0:
            local_pct = round(snapshot["requests_local"] / snapshot["requests_total"] * 100, 1)

        snapshot["local_percentage"] = local_pct
        snapshot["cost_saved_display"] = f"${snapshot['cost_saved_usd']:.4f}"
        snapshot["pricing_tier"] = DEFAULT_TIER
        return snapshot

    def reset(self):
        with self._lock:
            for key in list(self._data.keys()):
                if key in ("started_at",):
                    continue
                if isinstance(self._data[key], int):
                    self._data[key] = 0
                elif isinstance(self._data[key], float):
                    self._data[key] = 0.0
            self._data["last_reset"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._dirty = True
        self._flush()


def _detect_tier(model: str) -> str:
    model_lower = model.lower()
    if "opus" in model_lower:
        return "opus"
    if "haiku" in model_lower:
        return "haiku"
    return DEFAULT_TIER


stats = Stats()
