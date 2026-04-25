"""Request statistics and cost tracking with periodic disk persistence."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from mlx_task_router.config import CONFIG_DIR

STATS_FILE = CONFIG_DIR / "stats.json"

# Anthropic pricing per million tokens (as of 2025-Q2)
# Covers Claude 4, Claude 3.5, and Claude 3 model families
PRICING = {
    "opus_4": {"input": 15.00, "output": 75.00},
    "sonnet_4": {"input": 3.00, "output": 15.00},
    "sonnet_3_5": {"input": 3.00, "output": 15.00},
    "haiku_3_5": {"input": 0.80, "output": 4.00},
    "opus_3": {"input": 15.00, "output": 75.00},
    "sonnet_3": {"input": 3.00, "output": 15.00},
    "haiku_3": {"input": 0.25, "output": 1.25},
}
DEFAULT_TIER = "sonnet_4"

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
    m = model.lower()

    # Claude 4 family (e.g. claude-opus-4-*, claude-sonnet-4-*)
    if "opus-4" in m or "opus_4" in m:
        return "opus_4"
    if "sonnet-4" in m or "sonnet_4" in m:
        return "sonnet_4"

    # Claude 3.5 family (e.g. claude-3-5-sonnet-*, claude-3-5-haiku-*)
    if "3-5-haiku" in m or "3.5-haiku" in m or "3_5_haiku" in m:
        return "haiku_3_5"
    if "3-5-sonnet" in m or "3.5-sonnet" in m or "3_5_sonnet" in m:
        return "sonnet_3_5"

    # Claude 3 family (e.g. claude-3-opus-*, claude-3-sonnet-*, claude-3-haiku-*)
    if "3-opus" in m or "3_opus" in m:
        return "opus_3"
    if "3-haiku" in m or "3_haiku" in m:
        return "haiku_3"
    if "3-sonnet" in m or "3_sonnet" in m:
        return "sonnet_3"

    # Generic fallback patterns
    if "opus" in m:
        return "opus_4"
    if "haiku" in m:
        return "haiku_3_5"

    return DEFAULT_TIER


stats = Stats()
