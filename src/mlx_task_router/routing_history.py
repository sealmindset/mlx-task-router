"""Ring buffer of recent routing decisions for debugging and observability."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

_MAX_HISTORY = 100


@dataclass
class RoutingDecision:
    timestamp: float
    route: str
    forward_score: float
    signals: list[str]
    trigger: str
    message_preview: str
    model: str


class RoutingHistory:
    def __init__(self, max_entries: int = _MAX_HISTORY):
        self._lock = threading.Lock()
        self._buffer: list[RoutingDecision] = []
        self._max = max_entries

    def record(
        self,
        route: str,
        reason: str,
        trigger: str,
        message_text: str,
        model: str = "",
    ) -> None:
        # Parse forward score and signals from reason string
        fwd_score = 0.0
        signals: list[str] = []
        if "fwd=" in reason:
            try:
                score_part = reason.split("fwd=")[1].split(" ")[0]
                fwd_score = float(score_part)
            except (ValueError, IndexError):
                pass
            bracket_start = reason.find("[")
            bracket_end = reason.rfind("]")
            if bracket_start >= 0 and bracket_end > bracket_start:
                signals = [s.strip() for s in reason[bracket_start + 1:bracket_end].split(",") if s.strip()]
        else:
            signals = [reason]

        preview = message_text[:80] + ("..." if len(message_text) > 80 else "")

        decision = RoutingDecision(
            timestamp=time.time(),
            route=route,
            forward_score=fwd_score,
            signals=signals,
            trigger=trigger,
            message_preview=preview,
            model=model,
        )

        with self._lock:
            self._buffer.append(decision)
            if len(self._buffer) > self._max:
                self._buffer = self._buffer[-self._max:]

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            entries = list(self._buffer[-limit:])
        entries.reverse()
        return [asdict(e) for e in entries]

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()

    def summary(self) -> dict[str, Any]:
        with self._lock:
            entries = list(self._buffer)
        if not entries:
            return {"total": 0, "local": 0, "forward": 0, "local_pct": 0.0}
        local_count = sum(1 for e in entries if e.route == "local")
        return {
            "total": len(entries),
            "local": local_count,
            "forward": len(entries) - local_count,
            "local_pct": round(local_count / len(entries) * 100, 1),
        }


routing_history = RoutingHistory()
