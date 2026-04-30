"""Trust-But-Verify (TBV) — QA/QC routing verification system.

Spot-checks routing decisions using Claude as ground-truth validator.
Runs async in the background with zero latency impact on user requests.
Optional shadow mode dual-generates for direct comparison.

Feeds results to verify_tuner for dynamic router optimization.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from mlx_task_router.config import CONFIG_DIR, config

_VERIFY_LOG = CONFIG_DIR / "verify_log.jsonl"
_MAX_LOG_ENTRIES = 1000

# Rubric JSON schema sent to the verifier model
_RUBRIC_SCHEMA = {
    "type": "object",
    "properties": {
        "correctness": {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "minimum": 1, "maximum": 5},
                "explanation": {"type": "string"},
            },
            "required": ["score", "explanation"],
        },
        "completeness": {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "minimum": 1, "maximum": 5},
                "explanation": {"type": "string"},
            },
            "required": ["score", "explanation"],
        },
        "code_quality": {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "minimum": 1, "maximum": 5},
                "explanation": {"type": "string"},
            },
            "required": ["score", "explanation"],
        },
        "routing_appropriateness": {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "minimum": 1, "maximum": 5},
                "explanation": {"type": "string"},
            },
            "required": ["score", "explanation"],
        },
        "overall_pass": {"type": "boolean"},
        "could_be_local": {"type": "boolean"},
        "suggested_route": {"type": "string", "enum": ["local", "fast", "forward"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "correctness",
        "completeness",
        "code_quality",
        "routing_appropriateness",
        "overall_pass",
        "could_be_local",
        "suggested_route",
        "confidence",
    ],
}


@dataclass
class VerificationResult:
    """Single verification outcome."""

    timestamp: float
    request_hash: str
    route: str
    mode: str  # "async" or "shadow"
    strategy: str  # "local_check", "retroactive", "shadow_local", "heuristic"
    scores: dict[str, int] = field(default_factory=dict)
    overall_pass: bool = False
    could_be_local: bool = False
    suggested_route: str = ""
    confidence: float = 0.0
    explanation: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "request_hash": self.request_hash,
            "route": self.route,
            "mode": self.mode,
            "strategy": self.strategy,
            "scores": self.scores,
            "overall_pass": self.overall_pass,
            "could_be_local": self.could_be_local,
            "suggested_route": self.suggested_route,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "error": self.error,
        }


@dataclass
class VerificationTask:
    """Queued verification task."""

    request_messages: list[dict]
    response_text: str
    route: str
    forward_score: float
    trigger: str
    shadow_response: str | None = None
    strategy: str = "local_check"
    priority: int = 0  # higher = process first


class TBVEngine:
    """Trust-But-Verify engine — sampling, queuing, validation."""

    def __init__(self):
        self._queue: asyncio.Queue[VerificationTask] | None = None
        self._results: list[VerificationResult] = []
        self._total_verified: int = 0
        self._total_passed: int = 0
        self._running = False
        self._task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None
        self._recent_change_counter: int = 0
        self._load_history()

    def _load_history(self) -> None:
        """Load verification stats from log file."""
        if not _VERIFY_LOG.exists():
            return
        try:
            lines = _VERIFY_LOG.read_text().strip().split("\n")
            for line in lines[-_MAX_LOG_ENTRIES:]:
                entry = json.loads(line)
                self._total_verified += 1
                if entry.get("overall_pass"):
                    self._total_passed += 1
        except (json.JSONDecodeError, OSError):
            pass

    @property
    def pass_rate(self) -> float:
        if self._total_verified == 0:
            return 1.0
        return self._total_passed / self._total_verified

    @property
    def adaptive_sample_rate(self) -> float:
        """Calculate current adaptive sampling rate."""
        # Manual override takes precedence
        if config.verify_sample_rate > 0:
            return config.verify_sample_rate

        # Adaptive logic
        if self._recent_change_counter > 0:
            self._recent_change_counter -= 1
            return 0.30  # Burst after routing change

        if self._total_verified < 50:
            return 0.20  # Cold start

        if self.pass_rate >= 0.90:
            return 0.05  # Stable
        elif self.pass_rate < 0.85:
            return 0.15  # Degrading

        return 0.10  # Normal

    def should_sample(self, forward_score: float = 0.0, is_borderline: bool = False) -> bool:
        """Determine if this request should be verified."""
        if not config.verify_enabled:
            return False

        rate = self.adaptive_sample_rate
        # Borderline forwards get 2x rate (heuristic targeting)
        if is_borderline:
            rate = min(rate * 2, 1.0)

        return random.random() < rate

    def notify_routing_change(self) -> None:
        """Signal that routing config/weights changed — bump sample rate."""
        self._recent_change_counter = 20

    async def start(self) -> None:
        """Start the verification background worker."""
        if self._running:
            return
        self._queue = asyncio.Queue(maxsize=config.verify_queue_size)
        self._client = httpx.AsyncClient(timeout=60.0)
        self._running = True
        self._task = asyncio.create_task(self._worker())
        print("[tbv] Trust-But-Verify engine started")

    async def stop(self) -> None:
        """Stop the verification worker."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
        print("[tbv] Trust-But-Verify engine stopped")

    def enqueue(self, task: VerificationTask) -> bool:
        """Add a verification task to the queue. Returns False if queue is full."""
        if not self._running or self._queue is None:
            return False
        try:
            self._queue.put_nowait(task)
            return True
        except asyncio.QueueFull:
            return False

    async def _worker(self) -> None:
        """Background worker that processes verification tasks."""
        while self._running:
            try:
                task = await asyncio.wait_for(self._queue.get(), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                continue

            try:
                result = await self._verify(task)
                self._results.append(result)
                if len(self._results) > 200:
                    self._results = self._results[-200:]
                self._total_verified += 1
                if result.overall_pass:
                    self._total_passed += 1
                self._persist_result(result)
                # Notify tuner
                from mlx_task_router.verify_tuner import tuner
                tuner.process_result(result)
                if config.log_routing:
                    status = "PASS" if result.overall_pass else "FAIL"
                    print(f"[tbv] {status} route={result.route} "
                          f"scores={result.scores} strategy={result.strategy}")
            except Exception as e:
                print(f"[tbv] Verification error: {e}")
                traceback.print_exc()

    async def _verify(self, task: VerificationTask) -> VerificationResult:
        """Run Opus verification for a single task."""
        request_text = self._extract_request_text(task.request_messages)
        request_hash = hashlib.sha256(request_text.encode()).hexdigest()[:12]

        prompt = self._build_prompt(task)

        try:
            rubric_json = await self._call_verifier(prompt)
            parsed = json.loads(rubric_json)
            scores = {
                "correctness": parsed["correctness"]["score"],
                "completeness": parsed["completeness"]["score"],
                "code_quality": parsed["code_quality"]["score"],
                "routing_appropriateness": parsed["routing_appropriateness"]["score"],
            }
            return VerificationResult(
                timestamp=time.time(),
                request_hash=request_hash,
                route=task.route,
                mode="shadow" if task.shadow_response else "async",
                strategy=task.strategy,
                scores=scores,
                overall_pass=parsed.get("overall_pass", False),
                could_be_local=parsed.get("could_be_local", False),
                suggested_route=parsed.get("suggested_route", ""),
                confidence=parsed.get("confidence", 0.0),
                explanation=self._summarize_explanations(parsed),
            )
        except (json.JSONDecodeError, KeyError) as e:
            return VerificationResult(
                timestamp=time.time(),
                request_hash=request_hash,
                route=task.route,
                mode="shadow" if task.shadow_response else "async",
                strategy=task.strategy,
                error=f"Parse error: {e}",
            )
        except Exception as e:
            return VerificationResult(
                timestamp=time.time(),
                request_hash=request_hash,
                route=task.route,
                mode="shadow" if task.shadow_response else "async",
                strategy=task.strategy,
                error=str(e),
            )

    def _build_prompt(self, task: VerificationTask) -> str:
        """Build the Opus validation prompt."""
        request_text = self._extract_request_text(task.request_messages)

        shadow_section = ""
        if task.shadow_response:
            shadow_section = f"""
REFERENCE MODEL RESPONSE (Claude Opus):
{task.shadow_response}

Compare the local response against this reference.
"""

        if task.route == "forward" and task.strategy == "retroactive":
            return f"""You are a routing quality auditor for an AI coding assistant. A request was
forwarded to a frontier model (Claude). Evaluate whether a local 27B model
(Qwen3.6-27B on Apple Silicon) could have handled this request adequately.

ORIGINAL REQUEST:
{request_text}

CLAUDE'S RESPONSE:
{task.response_text}

Evaluate on these 4 axes (score 1-5):
1. **Correctness** — Would a 27B local model likely produce a correct answer here?
2. **Completeness** — Could a 27B model address all parts of the request?
3. **Code Quality** — Would a 27B model produce well-structured code?
4. **Routing Appropriateness** — Was forwarding to a frontier model necessary, or overkill?

Also determine:
- `could_be_local` (bool): Could a competent 27B local model handle this?
- `suggested_route`: "local", "fast", or "forward"
- `confidence` (0-1): How confident are you in this assessment?
- `overall_pass` (bool): Was the routing decision correct? (true = correctly forwarded)

Return ONLY valid JSON matching this schema:
{json.dumps(_RUBRIC_SCHEMA, indent=2)}"""

        return f"""You are a routing quality auditor for an AI coding assistant. Your job is to
evaluate whether a local language model (Qwen3.6-27B running on Apple Silicon)
produced an acceptable response to a coding request.

ORIGINAL REQUEST:
{request_text}

LOCAL MODEL RESPONSE:
{task.response_text}
{shadow_section}
Evaluate the local response on these 4 axes (score 1-5):
1. **Correctness** — Is the code/answer factually correct? Any bugs or errors?
2. **Completeness** — Does it address all parts of the request?
3. **Code Quality** — Is it well-structured, readable, follows best practices?
4. **Routing Appropriateness** — Was this request appropriate for a local 27B model,
   or should it have been forwarded to a frontier model?

Also determine:
- `could_be_local` (bool): Could a competent 27B local model handle this?
- `suggested_route`: "local", "fast", or "forward"
- `confidence` (0-1): How confident are you in this assessment?
- `overall_pass` (bool): Is the local response acceptable? (all axes >= {config.verify_min_score})

Return ONLY valid JSON matching this schema:
{json.dumps(_RUBRIC_SCHEMA, indent=2)}"""

    async def _call_verifier(self, prompt: str) -> str:
        """Call the verification model (Claude) and return raw response text."""
        headers = {
            "x-api-key": config.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": config.verify_model,
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}],
        }
        resp = await self._client.post(
            f"{config.anthropic_api_url}/v1/messages",
            headers=headers,
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"]
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        return text.strip()

    def _extract_request_text(self, messages: list[dict]) -> str:
        """Extract the last user message text."""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    text_parts = [
                        b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    return " ".join(text_parts)
                return content
        return ""

    def _summarize_explanations(self, parsed: dict) -> str:
        """Combine axis explanations into a brief summary."""
        parts = []
        for axis in ("correctness", "completeness", "code_quality", "routing_appropriateness"):
            if axis in parsed and "explanation" in parsed[axis]:
                parts.append(f"{axis}: {parsed[axis]['explanation']}")
        return " | ".join(parts)[:500]

    def _persist_result(self, result: VerificationResult) -> None:
        """Append result to JSONL log."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(_VERIFY_LOG, "a") as f:
                f.write(json.dumps(result.to_dict()) + "\n")
        except OSError:
            pass

    def reset(self) -> None:
        """Clear all verification data."""
        self._results.clear()
        self._total_verified = 0
        self._total_passed = 0
        self._recent_change_counter = 0
        try:
            _VERIFY_LOG.unlink(missing_ok=True)
        except OSError:
            pass

    def status(self) -> dict[str, Any]:
        """Return current TBV status."""
        return {
            "enabled": config.verify_enabled,
            "shadow_mode": config.verify_shadow_mode,
            "sample_rate": self.adaptive_sample_rate,
            "sample_rate_override": config.verify_sample_rate,
            "total_verified": self._total_verified,
            "total_passed": self._total_passed,
            "pass_rate": round(self.pass_rate, 3),
            "queue_depth": self._queue.qsize() if self._queue else 0,
            "queue_max": config.verify_queue_size,
            "running": self._running,
            "verify_model": config.verify_model,
            "auto_tune": config.verify_auto_tune,
            "recent_results": len(self._results),
        }

    def recent_results(self, limit: int = 20) -> list[dict]:
        """Return recent verification results."""
        return [r.to_dict() for r in self._results[-limit:]]


# Module-level singleton
tbv_engine = TBVEngine()
