"""Per-session routing statistics.

Tracks routing patterns grouped by session ID. Sessions are identified by:
  1. Request header: x-session-id, anthropic-session-id, or x-request-id prefix
  2. Auto-session: if no header, a new session starts after SESSION_GAP_SECONDS
     of inactivity (default 5 minutes).

Storage: in-memory ring buffer of the last MAX_SESSIONS sessions. No disk
persistence — sessions are ephemeral and reset on service restart.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from mlx_task_router.stats import PRICING, DEFAULT_TIER, _detect_tier

MAX_SESSIONS = int(os.getenv("MAX_SESSIONS", "50"))
SESSION_GAP_SECONDS = int(os.getenv("SESSION_GAP_SECONDS", "300"))
_MAX_DECISIONS_PER_SESSION = 50


@dataclass
class MiniDecision:
    """Compact routing decision stored per session."""

    timestamp: float
    route: str
    trigger: str
    forward_score: float
    message_preview: str


@dataclass
class SessionStats:
    """Aggregated stats for a single session."""

    session_id: str
    started_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    requests_total: int = 0
    requests_local: int = 0
    requests_forwarded: int = 0
    requests_cache: int = 0
    tokens_local_input: int = 0
    tokens_local_output: int = 0
    tokens_forwarded_input: int = 0
    tokens_forwarded_output: int = 0
    cost_saved_usd: float = 0.0
    top_triggers: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    decisions: list[MiniDecision] = field(default_factory=list)

    def record(
        self,
        route: str,
        trigger: str,
        forward_score: float,
        message_preview: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: str = "",
    ) -> None:
        now = time.time()
        self.last_activity = now
        self.requests_total += 1

        if route == "local":
            self.requests_local += 1
            self.tokens_local_input += input_tokens
            self.tokens_local_output += output_tokens
            tier = _detect_tier(model)
            pricing = PRICING[tier]
            saved = (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
            self.cost_saved_usd = round(self.cost_saved_usd + saved, 6)
        elif route == "cache":
            self.requests_cache += 1
        else:
            self.requests_forwarded += 1
            self.tokens_forwarded_input += input_tokens
            self.tokens_forwarded_output += output_tokens

        if trigger:
            self.top_triggers[trigger] += 1

        self.decisions.append(MiniDecision(
            timestamp=now,
            route=route,
            trigger=trigger,
            forward_score=forward_score,
            message_preview=message_preview[:80],
        ))
        if len(self.decisions) > _MAX_DECISIONS_PER_SESSION:
            self.decisions = self.decisions[-_MAX_DECISIONS_PER_SESSION:]

    @property
    def local_pct(self) -> float:
        if self.requests_total == 0:
            return 0.0
        return round(self.requests_local / self.requests_total * 100, 1)

    @property
    def duration_seconds(self) -> float:
        return round(self.last_activity - self.started_at, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "last_activity": self.last_activity,
            "duration_seconds": self.duration_seconds,
            "requests_total": self.requests_total,
            "requests_local": self.requests_local,
            "requests_forwarded": self.requests_forwarded,
            "requests_cache": self.requests_cache,
            "local_pct": self.local_pct,
            "tokens_local_input": self.tokens_local_input,
            "tokens_local_output": self.tokens_local_output,
            "tokens_forwarded_input": self.tokens_forwarded_input,
            "tokens_forwarded_output": self.tokens_forwarded_output,
            "cost_saved_usd": self.cost_saved_usd,
            "cost_saved_display": f"${self.cost_saved_usd:.4f}",
            "top_triggers": dict(
                sorted(self.top_triggers.items(), key=lambda x: x[1], reverse=True)[:10]
            ),
            "recent_decisions": [
                {
                    "timestamp": d.timestamp,
                    "route": d.route,
                    "trigger": d.trigger,
                    "forward_score": d.forward_score,
                    "message_preview": d.message_preview,
                }
                for d in self.decisions[-20:]
            ],
        }


class SessionTracker:
    """Track per-session routing stats with auto-session detection."""

    def __init__(self, max_sessions: int = MAX_SESSIONS, gap_seconds: int = SESSION_GAP_SECONDS):
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionStats] = {}
        self._session_order: list[str] = []
        self._max_sessions = max_sessions
        self._gap_seconds = gap_seconds
        self._auto_counter = 0
        self._last_activity = 0.0
        self._current_auto_id: str | None = None

    def _resolve_session_id(self, headers: dict[str, str] | None) -> str:
        """Resolve session ID from headers or auto-generate one."""
        if headers:
            for key in ("x-session-id", "anthropic-session-id"):
                val = headers.get(key, "")
                if val:
                    return val
            req_id = headers.get("x-request-id", "")
            if req_id and "-" in req_id:
                return req_id.rsplit("-", 1)[0]

        now = time.time()
        if self._current_auto_id is None or (now - self._last_activity) > self._gap_seconds:
            self._auto_counter += 1
            self._current_auto_id = f"auto-{self._auto_counter}"
        self._last_activity = now
        return self._current_auto_id

    def _get_or_create(self, session_id: str) -> SessionStats:
        """Get existing session or create a new one. Must hold self._lock."""
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionStats(session_id=session_id)
            self._session_order.append(session_id)
            if len(self._session_order) > self._max_sessions:
                oldest = self._session_order.pop(0)
                self._sessions.pop(oldest, None)
        return self._sessions[session_id]

    def record(
        self,
        route: str,
        trigger: str,
        forward_score: float,
        message_preview: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: str = "",
        headers: dict[str, str] | None = None,
    ) -> str:
        """Record a routing decision. Returns the session ID used."""
        session_id = self._resolve_session_id(headers)
        with self._lock:
            session = self._get_or_create(session_id)
            session.record(
                route=route,
                trigger=trigger,
                forward_score=forward_score,
                message_preview=message_preview,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model=model,
            )
        return session_id

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            return session.to_dict()

    def get_current_session(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._session_order:
                return None
            latest_id = self._session_order[-1]
            return self._sessions[latest_id].to_dict()

    def get_all_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(reversed(self._session_order[-limit:]))
            return [self._sessions[sid].to_dict() for sid in ids if sid in self._sessions]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            total = len(self._sessions)
            if total == 0:
                return {"total_sessions": 0, "active_sessions": 0, "current_session": None}
            now = time.time()
            active = sum(
                1 for s in self._sessions.values()
                if (now - s.last_activity) < self._gap_seconds
            )
            current = self._session_order[-1] if self._session_order else None
            return {
                "total_sessions": total,
                "active_sessions": active,
                "current_session": current,
            }

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._session_order.clear()
            self._auto_counter = 0
            self._current_auto_id = None
            self._last_activity = 0.0


session_tracker = SessionTracker()
