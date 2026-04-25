"""Routing feedback loop — tracks which triggers produce fallbacks.

Uses a dirty flag and periodic flush thread to avoid disk I/O on every
request.  Mirrors the pattern in stats.py.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from mlx_task_router.config import CONFIG_DIR

_FEEDBACK_FILE = CONFIG_DIR / "feedback.json"
_FLUSH_INTERVAL = 30  # seconds


class RoutingFeedback:
    def __init__(self):
        self._lock = threading.Lock()
        self._triggers: dict[str, dict[str, int]] = {}
        self._dirty = False
        self._flush_thread: threading.Thread | None = None
        self._running = False
        self._load()

    def _load(self) -> None:
        if _FEEDBACK_FILE.exists():
            try:
                data = json.loads(_FEEDBACK_FILE.read_text())
                self._triggers = data
            except (json.JSONDecodeError, OSError):
                pass

    def _flush(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            try:
                _FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
                _FEEDBACK_FILE.write_text(json.dumps(self._triggers, indent=2))
                self._dirty = False
            except OSError:
                pass

    def _flush_loop(self) -> None:
        while self._running:
            time.sleep(_FLUSH_INTERVAL)
            self._flush()

    def start(self) -> None:
        self._running = True
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

    def stop(self) -> None:
        self._running = False
        self._flush()

    def record_success(self, trigger: str) -> None:
        with self._lock:
            entry = self._triggers.setdefault(trigger, {"attempts": 0, "failures": 0})
            entry["attempts"] += 1
            self._dirty = True

    def record_failure(self, trigger: str) -> None:
        with self._lock:
            entry = self._triggers.setdefault(trigger, {"attempts": 0, "failures": 0})
            entry["attempts"] += 1
            entry["failures"] += 1
            self._dirty = True

    def _penalty_for(self, entry: dict[str, int]) -> float:
        if entry["attempts"] < 2:
            return 0.0
        rate = entry["failures"] / entry["attempts"]
        return -0.4 * rate if rate > 0.3 else 0.0

    def penalty(self, trigger: str) -> float:
        with self._lock:
            entry = self._triggers.get(trigger)
            if not entry:
                return 0.0
            return self._penalty_for(entry)

    def stats(self) -> dict:
        with self._lock:
            result = {}
            for trigger, entry in self._triggers.items():
                rate = entry["failures"] / entry["attempts"] if entry["attempts"] > 0 else 0
                result[trigger] = {
                    "attempts": entry["attempts"],
                    "failures": entry["failures"],
                    "failure_rate": f"{rate:.0%}",
                    "penalty": self._penalty_for(entry),
                }
            return result

    def reset(self) -> None:
        with self._lock:
            self._triggers.clear()
            self._dirty = True
        self._flush()


routing_feedback = RoutingFeedback()
