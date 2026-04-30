"""Self-annealing routing weights — auto-optimize signal weights from feedback.

Periodically analyzes feedback data and adjusts forward signal weights using
a simple gradient-free approach. Signals that lead to frequent fallbacks get
their weights increased (forward more aggressively for those patterns).
Signals that succeed locally get their weights decreased (keep local).

The annealer runs as a background thread, checking every N minutes.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from mlx_task_router.config import CONFIG_DIR

_ANNEAL_FILE = CONFIG_DIR / "annealing.json"
_ANNEAL_INTERVAL = int(os.getenv("ANNEAL_INTERVAL_SECONDS", "300"))
_MIN_SAMPLES = int(os.getenv("ANNEAL_MIN_SAMPLES", "20"))
_LEARNING_RATE = float(os.getenv("ANNEAL_LEARNING_RATE", "0.05"))

# Weight bounds — prevent any signal from going to extremes
_MIN_WEIGHT = 0.05
_MAX_WEIGHT = 1.0


class WeightAnnealer:
    def __init__(self):
        self._lock = threading.Lock()
        self._adjustments: dict[str, float] = {}
        self._history: list[dict[str, Any]] = []
        self._thread: threading.Thread | None = None
        self._running = False
        self._load()

    def _load(self) -> None:
        if _ANNEAL_FILE.exists():
            try:
                data = json.loads(_ANNEAL_FILE.read_text())
                self._adjustments = data.get("adjustments", {})
                self._history = data.get("history", [])
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self) -> None:
        try:
            _ANNEAL_FILE.parent.mkdir(parents=True, exist_ok=True)
            _ANNEAL_FILE.write_text(json.dumps({
                "adjustments": self._adjustments,
                "history": self._history[-50:],
            }, indent=2))
        except OSError:
            pass

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"[annealing] Started (interval={_ANNEAL_INTERVAL}s, lr={_LEARNING_RATE})")

    def stop(self) -> None:
        self._running = False
        self._save()

    def _run(self) -> None:
        while self._running:
            time.sleep(_ANNEAL_INTERVAL)
            if self._running:
                self._anneal_step()

    def _anneal_step(self) -> None:
        """Analyze feedback and adjust signal weights."""
        from mlx_task_router.feedback import routing_feedback

        fb = routing_feedback.stats()
        if not fb:
            return

        total_attempts = sum(v["attempts"] for v in fb.values())
        if total_attempts < _MIN_SAMPLES:
            return

        adjustments_made: dict[str, float] = {}

        with self._lock:
            for trigger, data in fb.items():
                attempts = data["attempts"]
                failures = data["failures"]
                if attempts < 3:
                    continue

                failure_rate = failures / attempts

                # Extract signal category from trigger (e.g., "exec:git" → "exec")
                category = trigger.split(":")[0] if ":" in trigger else trigger

                current_adj = self._adjustments.get(category, 0.0)

                # High failure rate → increase forward tendency (positive adjustment)
                # Low failure rate → decrease forward tendency (keep local)
                if failure_rate > 0.3:
                    delta = _LEARNING_RATE * (failure_rate - 0.1)
                elif failure_rate < 0.1 and attempts >= 5:
                    delta = -_LEARNING_RATE * 0.5
                else:
                    continue

                new_adj = max(-_MAX_WEIGHT, min(_MAX_WEIGHT, current_adj + delta))
                if abs(new_adj) < 0.01:
                    new_adj = 0.0
                self._adjustments[category] = new_adj
                adjustments_made[category] = new_adj

            if adjustments_made:
                self._history.append({
                    "timestamp": time.time(),
                    "adjustments": dict(adjustments_made),
                    "total_samples": total_attempts,
                })
                self._save()
                print(f"[annealing] Adjusted weights: {adjustments_made}")

        # Trigger embed router probe retraining periodically
        try:
            from mlx_task_router.embed_router import embed_router
            sample_count = embed_router.training_sample_count()
            from mlx_task_router.config import config as _cfg
            if sample_count >= _cfg.embed_min_samples and total_attempts % 50 == 0:
                embed_router.train()
        except Exception as e:
            print(f"[annealing] Embed probe retrain failed (non-fatal): {e}")

    def get_adjustment(self, signal_category: str) -> float:
        """Get the current weight adjustment for a signal category."""
        with self._lock:
            return self._adjustments.get(signal_category, 0.0)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "adjustments": dict(self._adjustments),
                "history_count": len(self._history),
                "last_anneal": self._history[-1] if self._history else None,
                "interval_seconds": _ANNEAL_INTERVAL,
                "learning_rate": _LEARNING_RATE,
                "min_samples": _MIN_SAMPLES,
            }

    def reset(self) -> None:
        with self._lock:
            self._adjustments.clear()
            self._history.clear()
            self._save()


weight_annealer = WeightAnnealer()
