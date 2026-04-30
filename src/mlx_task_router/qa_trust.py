"""QA Trust — graduated trust per request category with statistical evidence.

Tracks verification outcomes per auto-detected category. Each category progresses
through trust levels (unproven → building → trusted → proven) based on pass rate
and sample count. Trust levels determine gate behavior.
"""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from mlx_task_router.config import CONFIG_DIR, config

_EVIDENCE_FILE = CONFIG_DIR / "qa_evidence.json"


class TrustLevel(str, Enum):
    UNPROVEN = "unproven"
    BUILDING = "building"
    TRUSTED = "trusted"
    PROVEN = "proven"
    DEGRADED = "degraded"


@dataclass
class CategoryEvidence:
    """Evidence for a single request category."""

    category: str
    total_samples: int = 0
    pass_count: int = 0
    fail_count: int = 0
    last_pass_ts: float = 0.0
    last_fail_ts: float = 0.0
    recent_scores: list[int] = field(default_factory=list)
    gate_lower_override: float | None = None
    gate_upper_override: float | None = None

    @property
    def pass_rate(self) -> float:
        if self.total_samples == 0:
            return 0.0
        return self.pass_count / self.total_samples

    @property
    def trust_level(self) -> TrustLevel:
        """Determine trust level from evidence."""
        if self.total_samples < 20:
            return TrustLevel.UNPROVEN

        rate = self.pass_rate

        # Degraded: pass rate drops below 90%
        if rate < 0.90:
            return TrustLevel.DEGRADED

        # Proven: 100+ samples, ≥ 98% pass
        if (self.total_samples >= config.qa_trust_proven_samples
                and rate >= config.qa_trust_proven_threshold):
            return TrustLevel.PROVEN

        # Trusted: 50+ samples, ≥ 95% pass
        if (self.total_samples >= config.qa_trust_min_samples
                and rate >= config.qa_trust_pass_threshold):
            return TrustLevel.TRUSTED

        # Building: 20-49 samples, ≥ 80% pass
        if rate >= 0.80:
            return TrustLevel.BUILDING

        return TrustLevel.DEGRADED

    @property
    def confidence_interval_95(self) -> tuple[float, float]:
        """Wilson score confidence interval for pass rate."""
        if self.total_samples == 0:
            return (0.0, 0.0)
        n = self.total_samples
        p = self.pass_rate
        z = 1.96  # 95% confidence
        denominator = 1 + z * z / n
        center = (p + z * z / (2 * n)) / denominator
        spread = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denominator
        lower = max(0.0, center - spread)
        upper = min(1.0, center + spread)
        return (round(lower, 4), round(upper, 4))

    def record(self, passed: bool, score: int = 5) -> None:
        """Record a verification outcome."""
        self.total_samples += 1
        if passed:
            self.pass_count += 1
            self.last_pass_ts = time.time()
        else:
            self.fail_count += 1
            self.last_fail_ts = time.time()
        self.recent_scores.append(score)
        if len(self.recent_scores) > 50:
            self.recent_scores = self.recent_scores[-50:]

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API response."""
        ci = self.confidence_interval_95
        return {
            "category": self.category,
            "trust_level": self.trust_level.value,
            "total_samples": self.total_samples,
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "pass_rate": round(self.pass_rate, 4),
            "confidence_interval_95": list(ci),
            "last_failure": self.last_fail_ts if self.last_fail_ts > 0 else None,
            "gate_lower_override": self.gate_lower_override,
            "gate_upper_override": self.gate_upper_override,
            "recent_scores": self.recent_scores[-10:],
        }


class QATrust:
    """Manages per-category trust evidence and graduated gate boundaries."""

    def __init__(self):
        self._lock = threading.Lock()
        self._categories: dict[str, CategoryEvidence] = {}
        self._total_gated: int = 0
        self._total_bypassed: int = 0
        self._total_swapped: int = 0  # Claude response delivered instead of local
        self._shadow_cost_tokens: int = 0
        self._load()

    def _load(self) -> None:
        """Load persisted evidence from disk."""
        if not _EVIDENCE_FILE.exists():
            return
        try:
            data = json.loads(_EVIDENCE_FILE.read_text())
            for cat_data in data.get("categories", []):
                cat = cat_data["category"]
                ev = CategoryEvidence(
                    category=cat,
                    total_samples=cat_data.get("total_samples", 0),
                    pass_count=cat_data.get("pass_count", 0),
                    fail_count=cat_data.get("fail_count", 0),
                    last_pass_ts=cat_data.get("last_pass_ts", 0.0),
                    last_fail_ts=cat_data.get("last_fail_ts", 0.0),
                    recent_scores=cat_data.get("recent_scores", []),
                    gate_lower_override=cat_data.get("gate_lower_override"),
                    gate_upper_override=cat_data.get("gate_upper_override"),
                )
                self._categories[cat] = ev
            self._total_gated = data.get("total_gated", 0)
            self._total_bypassed = data.get("total_bypassed", 0)
            self._total_swapped = data.get("total_swapped", 0)
            self._shadow_cost_tokens = data.get("shadow_cost_tokens", 0)
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    def _save(self) -> None:
        """Persist evidence to disk."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "categories": [ev.to_dict() for ev in self._categories.values()],
                "total_gated": self._total_gated,
                "total_bypassed": self._total_bypassed,
                "total_swapped": self._total_swapped,
                "shadow_cost_tokens": self._shadow_cost_tokens,
            }
            _EVIDENCE_FILE.write_text(json.dumps(data, indent=2))
        except OSError:
            pass

    def record_outcome(
        self,
        category: str,
        passed: bool,
        score: int = 5,
        swapped: bool = False,
        shadow_tokens: int = 0,
    ) -> None:
        """Record a gate validation outcome."""
        with self._lock:
            if category not in self._categories:
                self._categories[category] = CategoryEvidence(category=category)
            self._categories[category].record(passed, score)
            self._total_gated += 1
            if swapped:
                self._total_swapped += 1
            self._shadow_cost_tokens += shadow_tokens
            self._update_gate_overrides(category)
            self._save()

    def record_bypass(self) -> None:
        """Record a request that bypassed the gate (confident local)."""
        with self._lock:
            self._total_bypassed += 1

    def _update_gate_overrides(self, category: str) -> None:
        """Narrow or widen gate boundaries based on trust level."""
        ev = self._categories[category]
        level = ev.trust_level
        base_lower = config.qa_gate_lower
        base_upper = config.qa_gate_upper

        if level == TrustLevel.PROVEN:
            # Skip gate entirely — treat as confident local
            ev.gate_lower_override = base_upper  # lower > upper means skip
            ev.gate_upper_override = base_upper
        elif level == TrustLevel.TRUSTED:
            # Narrow gate by ±0.1
            ev.gate_lower_override = base_lower + 0.1
            ev.gate_upper_override = base_upper - 0.1
        elif level == TrustLevel.DEGRADED:
            # Widen gate by ±0.1
            ev.gate_lower_override = max(0.1, base_lower - 0.1)
            ev.gate_upper_override = min(1.0, base_upper + 0.1)
        else:
            ev.gate_lower_override = None
            ev.gate_upper_override = None

    def should_gate(self, forward_score: float, category: str | None = None) -> bool:
        """Determine if a request should enter the gate based on score and category trust."""
        if not config.qa_gate_enabled:
            return False

        gate_lower = config.qa_gate_lower
        gate_upper = config.qa_gate_upper

        # Apply category-specific overrides if available
        if category:
            with self._lock:
                ev = self._categories.get(category)
                if ev and ev.gate_lower_override is not None:
                    gate_lower = ev.gate_lower_override
                if ev and ev.gate_upper_override is not None:
                    gate_upper = ev.gate_upper_override

        # If lower >= upper (proven category), never gate
        if gate_lower >= gate_upper:
            return False

        return gate_lower <= forward_score < gate_upper

    def get_gate_bounds(self, category: str | None = None) -> tuple[float, float]:
        """Get effective gate bounds for a category."""
        gate_lower = config.qa_gate_lower
        gate_upper = config.qa_gate_upper
        if category:
            with self._lock:
                ev = self._categories.get(category)
                if ev and ev.gate_lower_override is not None:
                    gate_lower = ev.gate_lower_override
                if ev and ev.gate_upper_override is not None:
                    gate_upper = ev.gate_upper_override
        return (gate_lower, gate_upper)

    def get_category(self, name: str) -> CategoryEvidence | None:
        """Get evidence for a specific category."""
        with self._lock:
            return self._categories.get(name)

    def get_all_categories(self) -> list[dict[str, Any]]:
        """Get all category evidence as dicts."""
        with self._lock:
            return [ev.to_dict() for ev in sorted(
                self._categories.values(),
                key=lambda e: e.total_samples,
                reverse=True,
            )]

    def overall_quality_score(self) -> dict[str, Any]:
        """Compute the overall quality assurance score across all categories."""
        with self._lock:
            total_samples = sum(ev.total_samples for ev in self._categories.values())
            total_pass = sum(ev.pass_count for ev in self._categories.values())

            if total_samples == 0:
                return {
                    "score": None,
                    "confidence_interval_95": None,
                    "total_validated": 0,
                    "categories_count": 0,
                    "proven_count": 0,
                    "message": "No validation data yet. Enable QA gate to begin building evidence.",
                }

            rate = total_pass / total_samples
            # Wilson interval
            n = total_samples
            z = 1.96
            denom = 1 + z * z / n
            center = (rate + z * z / (2 * n)) / denom
            spread = z * math.sqrt((rate * (1 - rate) + z * z / (4 * n)) / n) / denom
            ci_lower = max(0.0, center - spread)
            ci_upper = min(1.0, center + spread)

            proven = sum(1 for ev in self._categories.values()
                         if ev.trust_level == TrustLevel.PROVEN)
            trusted = sum(1 for ev in self._categories.values()
                          if ev.trust_level == TrustLevel.TRUSTED)
            total_cats = len(self._categories)

            return {
                "score": round(rate * 100, 1),
                "confidence_interval_95": [round(ci_lower * 100, 1), round(ci_upper * 100, 1)],
                "total_validated": total_samples,
                "categories_count": total_cats,
                "proven_count": proven,
                "trusted_count": trusted,
                "message": (
                    f"With 95% confidence, at least {round(ci_lower * 100, 1)}% of "
                    f"local responses are equivalent to Claude based on "
                    f"{total_samples} validated responses across {total_cats} categories."
                ),
            }

    def cost_summary(self) -> dict[str, Any]:
        """Compute cost analysis for shadow validation."""
        with self._lock:
            # Approximate cost: $3/M input, $15/M output for Sonnet
            est_cost = self._shadow_cost_tokens * 0.000003
            return {
                "total_gated": self._total_gated,
                "total_bypassed": self._total_bypassed,
                "total_swapped": self._total_swapped,
                "shadow_tokens_used": self._shadow_cost_tokens,
                "estimated_shadow_cost_usd": round(est_cost, 4),
                "gate_hit_rate": (
                    round(self._total_gated / max(1, self._total_gated + self._total_bypassed), 4)
                ),
                "swap_rate": (
                    round(self._total_swapped / max(1, self._total_gated), 4)
                ),
            }

    def status(self) -> dict[str, Any]:
        """Full status for API."""
        quality = self.overall_quality_score()
        cost = self.cost_summary()
        return {
            "enabled": config.qa_gate_enabled,
            "quality": quality,
            "cost": cost,
            "gate_bounds": {
                "lower": config.qa_gate_lower,
                "upper": config.qa_gate_upper,
            },
            "categories_summary": {
                "total": len(self._categories),
                "proven": sum(1 for ev in self._categories.values()
                              if ev.trust_level == TrustLevel.PROVEN),
                "trusted": sum(1 for ev in self._categories.values()
                               if ev.trust_level == TrustLevel.TRUSTED),
                "building": sum(1 for ev in self._categories.values()
                                if ev.trust_level == TrustLevel.BUILDING),
                "degraded": sum(1 for ev in self._categories.values()
                                if ev.trust_level == TrustLevel.DEGRADED),
            },
        }

    def reset(self) -> None:
        """Clear all evidence."""
        with self._lock:
            self._categories.clear()
            self._total_gated = 0
            self._total_bypassed = 0
            self._total_swapped = 0
            self._shadow_cost_tokens = 0
            try:
                _EVIDENCE_FILE.unlink(missing_ok=True)
            except OSError:
                pass


# Module-level singleton
qa_trust = QATrust()
