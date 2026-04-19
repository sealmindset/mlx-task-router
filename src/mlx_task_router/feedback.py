"""Routing feedback loop — tracks which triggers produce fallbacks."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from mlx_task_router.config import CONFIG_DIR

_FEEDBACK_FILE = CONFIG_DIR / "feedback.json"


class RoutingFeedback:
    def __init__(self):
        self._lock = threading.Lock()
        self._triggers: dict[str, dict[str, int]] = {}
        self._load()

    def _load(self) -> None:
        if _FEEDBACK_FILE.exists():
            try:
                data = json.loads(_FEEDBACK_FILE.read_text())
                self._triggers = data
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self) -> None:
        try:
            _FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
            _FEEDBACK_FILE.write_text(json.dumps(self._triggers, indent=2))
        except OSError:
            pass

    def record_success(self, trigger: str) -> None:
        with self._lock:
            entry = self._triggers.setdefault(trigger, {"attempts": 0, "failures": 0})
            entry["attempts"] += 1
            self._save()

    def record_failure(self, trigger: str) -> None:
        with self._lock:
            entry = self._triggers.setdefault(trigger, {"attempts": 0, "failures": 0})
            entry["attempts"] += 1
            entry["failures"] += 1
            self._save()

    def penalty(self, trigger: str) -> float:
        with self._lock:
            entry = self._triggers.get(trigger)
            if not entry or entry["attempts"] < 2:
                return 0.0
            rate = entry["failures"] / entry["attempts"]
            # Scale: 50% failure rate = -0.2, 100% = -0.4
            return -0.4 * rate if rate > 0.3 else 0.0

    def stats(self) -> dict:
        with self._lock:
            result = {}
            for trigger, entry in self._triggers.items():
                rate = entry["failures"] / entry["attempts"] if entry["attempts"] > 0 else 0
                result[trigger] = {
                    "attempts": entry["attempts"],
                    "failures": entry["failures"],
                    "failure_rate": f"{rate:.0%}",
                    "penalty": self.penalty(trigger),
                }
            return result

    def reset(self) -> None:
        with self._lock:
            self._triggers.clear()
            self._save()


routing_feedback = RoutingFeedback()
