"""Verify Tuner — dynamic router adjustment from TBV verification results.

Processes verification results and applies small, bounded adjustments to
routing signal weights. Adjustments decay over time if not reinforced.
Integrates with the existing annealing system.
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any

from mlx_task_router.config import CONFIG_DIR, config

_ADJUSTMENTS_FILE = CONFIG_DIR / "verify_adjustments.json"
_DECAY_HALF_LIFE = 200  # verifications before adjustment halves
_MAX_ADJUSTMENT = 0.3  # hard bound per signal
_STEP_SIZE = 0.02  # base adjustment step
_CONFIDENCE_THRESHOLD = 0.7  # minimum confidence to act on


class VerifyTuner:
    """Applies bounded, decaying adjustments to router weights from TBV results."""

    def __init__(self):
        self._lock = threading.Lock()
        self._adjustments: dict[str, float] = {
            "threshold": 0.0,
            "complexity": 0.0,
            "code_generation": 0.0,
            "extended_convo": 0.0,
            "long_msg": 0.0,
            "question_chain": 0.0,
            "many_tools": 0.0,
            "trivial_sensitivity": 0.0,
        }
        self._learned_patterns: list[dict] = []
        self._total_processed: int = 0
        self._last_decay_at: int = 0
        self._history: list[dict] = []
        self._load()

    def _load(self) -> None:
        """Load persisted adjustments from disk."""
        if not _ADJUSTMENTS_FILE.exists():
            return
        try:
            data = json.loads(_ADJUSTMENTS_FILE.read_text())
            self._adjustments.update(data.get("adjustments", {}))
            self._learned_patterns = data.get("learned_patterns", [])
            self._total_processed = data.get("total_processed", 0)
            self._last_decay_at = data.get("last_decay_at", 0)
        except (json.JSONDecodeError, OSError):
            pass

    def _save(self) -> None:
        """Persist adjustments to disk."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "adjustments": self._adjustments,
                "learned_patterns": self._learned_patterns[-50:],
                "total_processed": self._total_processed,
                "last_decay_at": self._last_decay_at,
            }
            _ADJUSTMENTS_FILE.write_text(json.dumps(data, indent=2))
        except OSError:
            pass

    def process_result(self, result: Any) -> None:
        """Process a single verification result and adjust weights."""
        if not config.verify_auto_tune:
            return
        if result.error:
            return
        if result.confidence < _CONFIDENCE_THRESHOLD:
            return

        with self._lock:
            self._total_processed += 1
            self._maybe_decay()

            action = self._determine_action(result)
            if action:
                self._apply_action(action, result)
                self._history.append({
                    "timestamp": time.time(),
                    "action": action,
                    "route": result.route,
                    "scores": result.scores,
                    "strategy": result.strategy,
                })
                if len(self._history) > 100:
                    self._history = self._history[-100:]
                self._save()

    def _determine_action(self, result: Any) -> str | None:
        """Determine what tuning action to take based on result."""
        min_score = config.verify_min_score

        # Fast model specific — check before generic local/fast handling
        if result.route == "fast":
            has_failure = any(score < min_score for score in result.scores.values())
            if has_failure and result.suggested_route == "local":
                return "reduce_trivial"  # Fast failed but main model would pass

        if result.route in ("local", "fast"):
            # Check if local response failed
            failed_axes = [
                axis for axis, score in result.scores.items()
                if score < min_score
            ]
            if failed_axes:
                return "increase_forward"  # Local failed → route more to forward
            if all(score == 5 for score in result.scores.values()):
                return "decrease_forward"  # Perfect local → keep more local

        elif result.route == "forward":
            if result.could_be_local and result.confidence >= 0.8:
                return "lower_threshold"  # Missed local opportunity
            if not result.could_be_local and result.suggested_route == "forward":
                return "raise_threshold"  # Correctly forwarded (confirm)

        return None

    def _apply_action(self, action: str, result: Any) -> None:
        """Apply a bounded adjustment based on the action."""
        step = _STEP_SIZE * result.confidence

        if action == "increase_forward":
            # Increase forward signal weights (makes routing less local)
            self._adjust("complexity", step)
            self._adjust("code_generation", step * 0.5)

        elif action == "decrease_forward":
            # Decrease forward signal weights (keeps more local)
            self._adjust("complexity", -step)
            self._adjust("code_generation", -step * 0.5)

        elif action == "lower_threshold":
            # Lower routing threshold (more stays local)
            self._adjust("threshold", -step)

        elif action == "raise_threshold":
            # Raise routing threshold (more stays local — confirming correct forward)
            self._adjust("threshold", step * 0.3)  # Smaller step for confirmation

        elif action == "reduce_trivial":
            # Make trivial detection less aggressive
            self._adjust("trivial_sensitivity", step)

    def _adjust(self, signal: str, delta: float) -> None:
        """Apply bounded adjustment to a signal."""
        if signal not in self._adjustments:
            self._adjustments[signal] = 0.0
        new_val = self._adjustments[signal] + delta
        # Hard bounds
        self._adjustments[signal] = max(-_MAX_ADJUSTMENT, min(_MAX_ADJUSTMENT, new_val))

    def _maybe_decay(self) -> None:
        """Apply exponential decay if enough verifications have passed."""
        steps_since_decay = self._total_processed - self._last_decay_at
        if steps_since_decay < 10:
            return

        decay_factor = math.exp(-math.log(2) * steps_since_decay / _DECAY_HALF_LIFE)
        for key in self._adjustments:
            self._adjustments[key] *= decay_factor

        # Decay learned patterns
        self._learned_patterns = [
            p for p in self._learned_patterns
            if p.get("strength", 0) * decay_factor > 0.01
        ]
        for p in self._learned_patterns:
            p["strength"] = p.get("strength", 0.5) * decay_factor

        self._last_decay_at = self._total_processed

    def get_adjustment(self, signal: str) -> float:
        """Get current adjustment for a signal. Used by router."""
        with self._lock:
            return self._adjustments.get(signal, 0.0)

    def get_threshold_adjustment(self) -> float:
        """Get threshold adjustment (applied in classify)."""
        with self._lock:
            return self._adjustments.get("threshold", 0.0)

    def get_all_adjustments(self) -> dict[str, float]:
        """Get all current adjustments."""
        with self._lock:
            return dict(self._adjustments)

    def get_learned_patterns(self) -> list[dict]:
        """Get learned forward patterns."""
        with self._lock:
            return list(self._learned_patterns)

    def reset(self) -> None:
        """Clear all adjustments and learned patterns."""
        with self._lock:
            for key in self._adjustments:
                self._adjustments[key] = 0.0
            self._learned_patterns.clear()
            self._total_processed = 0
            self._last_decay_at = 0
            self._history.clear()
            try:
                _ADJUSTMENTS_FILE.unlink(missing_ok=True)
            except OSError:
                pass

    def status(self) -> dict[str, Any]:
        """Return current tuner status."""
        with self._lock:
            non_zero = {k: round(v, 4) for k, v in self._adjustments.items() if abs(v) > 0.001}
            return {
                "enabled": config.verify_auto_tune,
                "total_processed": self._total_processed,
                "adjustments": non_zero,
                "learned_patterns_count": len(self._learned_patterns),
                "decay_half_life": _DECAY_HALF_LIFE,
                "max_adjustment": _MAX_ADJUSTMENT,
                "step_size": _STEP_SIZE,
                "recent_actions": self._history[-10:],
            }


# Module-level singleton
tuner = VerifyTuner()
