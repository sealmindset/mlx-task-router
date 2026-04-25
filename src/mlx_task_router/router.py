"""Classify incoming requests as LOCAL (MLX) or FORWARD (Anthropic API).

Routing preference: DEFAULT is LOCAL — the router prefers to handle requests
locally for speed and cost savings. Only forwards when clear signals indicate
the task exceeds local model capability.

Fail-open safety: On any error (local generation failure, timeout, etc.),
requests automatically forward to Claude API (handled in server.py). The
service never blocks or degrades when the cloud is available.

Forward signals (push toward FORWARD):
  HARD    thinking requested (budget_tokens present)
  HARD    context too large
  HARD    model not loaded
  HARD    @cloud override
  +0.5    complexity pattern detected (explain, refactor, debug…)
  +0.3    code generation request (write a function, implement, create)
  +0.4    extended conversation (>20 non-tool-result user turns)
  +0.2    long message (>500 chars)
  +0.2    question chain (multiple ?)
  +0.2    many tools (>15 tool definitions)
  +0.4    very many tools (>30 tool definitions)

Local reinforcement signals (reduce forward score):
  HARD    @local override
  -0.3    executable detected as first word
  -0.3    CLI action phrase
  -0.1    short message (<80 chars)
"""

from __future__ import annotations

import re
import shutil
from functools import lru_cache
from typing import Any

from mlx_task_router.annealing import weight_annealer
from mlx_task_router.config import config
from mlx_task_router.feedback import routing_feedback
from mlx_task_router.local import model_manager


class Route:
    LOCAL = "local"
    FORWARD = "forward"


FORWARD_THRESHOLD = config.routing_threshold


def _adaptive_threshold() -> float:
    """Auto-calibrate forward threshold from feedback data."""
    if not config.adaptive_routing:
        return FORWARD_THRESHOLD

    fb = routing_feedback.stats()
    if not fb:
        return FORWARD_THRESHOLD

    total_attempts = sum(t["attempts"] for t in fb.values())
    total_failures = sum(t["failures"] for t in fb.values())

    if total_attempts < 20:
        return FORWARD_THRESHOLD

    failure_rate = total_failures / total_attempts
    # High local failure rate → LOWER the forward threshold (forward more)
    # Low local failure rate → RAISE the forward threshold (keep more local)
    adjustment = (failure_rate - 0.10) * -0.5
    return max(0.2, min(0.8, FORWARD_THRESHOLD + adjustment))

# --- Forward signal weights (positive = push toward FORWARD) ---
FWD_COMPLEXITY = 0.5
FWD_CODE_GENERATION = 0.3
FWD_EXTENDED_CONVO = 0.4
FWD_LONG_MSG = 0.2
FWD_QUESTION_CHAIN = 0.2
FWD_MANY_TOOLS = 0.2
FWD_VERY_MANY_TOOLS = 0.4

# --- Local reinforcement weights (negative = pull toward LOCAL) ---
LOCAL_EXEC_FIRST_WORD = -0.3
LOCAL_EXEC_IN_TEXT = -0.15
LOCAL_ACTION_PHRASE = -0.3
LOCAL_SHORT_MSG = -0.1

# ---------------------------------------------------------------------------
# Forward signals — complexity patterns that should go to Claude
# ---------------------------------------------------------------------------
_COMPLEXITY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(explain|understand|walk\s+me\s+through|break\s+down|how\s+does|what\s+does|what\s+is|what\s+are|analyze|analyse)\b",
        r"\bwhy\s+(does|is|are|do|did|would|should|can't|doesn't|isn't)\b",
        r"\b(refactor|rewrite|redesign|architect)\s+(a|an|the|this|new|my)\b",
        r"\b(fix|resolve|solve|troubleshoot|debug)\s+(this|the|a|an)\s+(\w+\s+)?(bug|error|issue|problem|crash|failure|regression|leak|vulnerability|bottleneck)\b",
        r"\b(security\s+review|vulnerability|CVE|exploit|injection|XSS|CSRF|penetration\s+test)\b",
        r"\b(code\s+review|review\s+(the|this|my)\s+code|look\s+at\s+(the|this|my)\s+code)\b",
        r"\b(compare|contrast|trade-?off|pros?\s+and\s+cons?|which\s+is\s+better)\b",
        r"\b(help\s+me|can\s+you|could\s+you|I\s+need|I\s+want)\s+(understand|figure\s+out|think\s+about|decide)\b",
        r"\b(plan|design|strategy|approach|architecture|proposal|roadmap)\b",
    ]
]

# Code generation — request is asking the model to WRITE substantial code
_CODE_GEN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(write|develop|create|implement)\s+(a|an|the|this|new|my)\s+(function|class|module|component|service|api|endpoint|test|script)\b",
        r"\b(scaffold|boilerplate|skeleton|stub)\b",
        r"\b(migrate|convert|port)\s+(to|from|the|this|between)\b",
        r"\b(write|create|draft)\s+(the\s+)?(docs|documentation|readme|docstring)\b",
    ]
]

# Words that look like executables but aren't CLI commands in this context
_EXECUTABLE_IGNORE = frozenset({
    "a", "an", "the", "this", "that", "it", "is", "are", "was", "were",
    "be", "do", "did", "has", "have", "had", "if", "in", "on", "at",
    "to", "for", "of", "or", "and", "not", "no", "yes", "but", "so",
    "my", "me", "we", "us", "you", "he", "she", "they", "all",
    "can", "will", "would", "could", "should", "may", "might",
    "what", "when", "where", "which", "who", "how", "why",
    "new", "old", "now", "then", "here", "there", "just", "also",
    "test", "true", "false", "ok", "yes", "no", "please",
    "time", "date", "file", "open", "more", "less", "sort", "clear",
    "script", "read", "write", "print", "set", "let", "env",
    "id", "as", "by", "up",
    "compare", "security", "plan", "design", "optimize", "migrate",
})

# CLI action phrases — natural language that implies CLI intent
_CLI_ACTION_PHRASES: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(commit\s+(and\s+)?push|push\s+(to\s+)?(remote|origin|github|upstream))\b",
        r"\b(stage|unstage)\s+(all|files|changes|everything)\b",
        r"\b(amend|squash)\s+(the\s+)?(last\s+)?(commit)\b",
        r"\b(run|execute)\s+(the\s+)?(tests|linter|formatter|build|ci|pipeline|migration|seeds?)\b",
        r"\b(install|setup)\s+(the\s+)?(dependencies|deps|packages|requirements)\b",
        r"\b(create|delete|rename)\s+(a\s+)?(branch|tag|file|folder|directory)\b",
        r"\b(clean\s+up|prune|purge|clear)\s+(the\s+)?(cache|build|dist|node_modules|target)\b",
        r"\b(check|view|list|show)\s+(the\s+)?(status|logs?|branches|tags|remotes|issues|prs?)\b",
        r"\b(build|compile|rebuild)\s+(the\s+)?(project|app|package)\b",
        r"\b(cherry[- ]?pick|rebase|merge)\s+(the\s+)?(branch|commit|pr)\b",
        r"\b(lint|format|type[- ]?check)\s+(the\s+)?(code|files?|project)\b",
        r"\b(deploy|publish|release)\s+(to\s+)?(staging|prod|npm|pypi)\b",
        r"\b(start|stop|restart)\s+(the\s+)?(server|service|container|docker)\b",
        r"\b(tail|grep|cat|head)\s+(the\s+)?(logs?|file|output)\b",
        r"\bkill\s+(the\s+)?(process|server|port)\b",
    ]
]

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]*")


@lru_cache(maxsize=2048)
def _is_executable(name: str) -> bool:
    return shutil.which(name) is not None


def _extract_executable_candidates(text: str) -> list[str]:
    words = _WORD_RE.findall(text)
    candidates = []
    for word in words:
        lower = word.lower()
        if lower in _EXECUTABLE_IGNORE:
            continue
        if len(lower) < 2:
            continue
        candidates.append(lower)
    return candidates


def _first_meaningful_word(text: str) -> str | None:
    """Get the first non-ignored word from the message."""
    for word in _WORD_RE.findall(text):
        lower = word.lower()
        if lower not in _EXECUTABLE_IGNORE and len(lower) >= 2:
            return lower
    return None


def _count_turns(messages: list[Any]) -> int:
    """Count user turns in the conversation, excluding tool_result messages.

    Tool results are user-role messages that contain tool_result content blocks.
    They represent automated round-trips, not genuine user complexity.
    """
    count = 0
    for msg in messages:
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role != "user":
            continue
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if isinstance(content, list):
            is_tool_result = any(
                (b.get("type") if isinstance(b, dict) else getattr(b, "type", None)) == "tool_result"
                for b in content
            )
            if is_tool_result:
                continue
        count += 1
    return count


def _score_forward(text: str, messages: list[Any], num_tools: int = 0) -> tuple[float, list[str], str]:
    """Score how strongly a request should FORWARD. Higher = more likely to forward.

    Returns (forward_score, reasons, trigger).
    """
    score = 0.0
    reasons: list[str] = []
    trigger = ""

    # --- Forward signals (positive = push toward Claude) ---

    # Complexity patterns
    for pattern in _COMPLEXITY_PATTERNS:
        m = pattern.search(text)
        if m:
            score += FWD_COMPLEXITY
            reasons.append(f"complex:'{m.group()}' +{FWD_COMPLEXITY}")
            trigger = f"complex:{m.group().lower()}"
            break

    # Code generation patterns
    for pattern in _CODE_GEN_PATTERNS:
        m = pattern.search(text)
        if m:
            score += FWD_CODE_GENERATION
            reasons.append(f"codegen:'{m.group()}' +{FWD_CODE_GENERATION}")
            if not trigger:
                trigger = f"codegen:{m.group().lower()}"
            break

    # Extended conversation (>20 non-tool-result user turns)
    turns = _count_turns(messages)
    if turns > 20:
        score += FWD_EXTENDED_CONVO
        reasons.append(f"turns:{turns} +{FWD_EXTENDED_CONVO}")

    # Long message (>500 chars)
    if len(text) > 500:
        score += FWD_LONG_MSG
        reasons.append(f"long({len(text)}ch) +{FWD_LONG_MSG}")

    # Question chain (multiple ?)
    q_count = text.count("?")
    if q_count >= 2:
        score += FWD_QUESTION_CHAIN
        reasons.append(f"questions:{q_count} +{FWD_QUESTION_CHAIN}")

    # Many tool definitions — harder for local models to handle
    if num_tools > 30:
        score += FWD_VERY_MANY_TOOLS
        reasons.append(f"tools:{num_tools} +{FWD_VERY_MANY_TOOLS}")
    elif num_tools > 15:
        score += FWD_MANY_TOOLS
        reasons.append(f"tools:{num_tools} +{FWD_MANY_TOOLS}")

    # --- Local reinforcement (negative = pull toward local) ---

    # CLI action phrases
    for pattern in _CLI_ACTION_PHRASES:
        m = pattern.search(text)
        if m:
            score += LOCAL_ACTION_PHRASE
            reasons.append(f"action:'{m.group()}' {LOCAL_ACTION_PHRASE}")
            if not trigger:
                trigger = f"action:{m.group().lower()}"
            break

    # Executable detection
    first_word = _first_meaningful_word(text)
    candidates = _extract_executable_candidates(text)
    first_word_scored = False

    for candidate in candidates:
        if _is_executable(candidate):
            if candidate == first_word and not first_word_scored:
                score += LOCAL_EXEC_FIRST_WORD
                reasons.append(f"exec:'{candidate}'(first) {LOCAL_EXEC_FIRST_WORD}")
                first_word_scored = True
            elif not first_word_scored or candidate != first_word:
                score += LOCAL_EXEC_IN_TEXT
                reasons.append(f"exec:'{candidate}' {LOCAL_EXEC_IN_TEXT}")
            if not trigger:
                trigger = f"exec:{candidate}"
            break

    # Short message reinforcement — only when no forward signals fired
    if len(text) < 80 and score <= 0:
        score += LOCAL_SHORT_MSG
        reasons.append(f"short {LOCAL_SHORT_MSG}")

    # --- Feedback adjustment ---
    if trigger:
        penalty = routing_feedback.penalty(trigger)
        if penalty != 0.0:
            score += penalty
            reasons.append(f"feedback:{trigger} {penalty:.2f}")

    # --- Self-annealing weight adjustments ---
    for category in ("complex", "codegen", "action", "exec"):
        adj = weight_annealer.get_adjustment(category)
        if adj != 0.0:
            score += adj
            reasons.append(f"anneal:{category} {adj:+.2f}")

    return score, reasons, trigger


def _get_latest_user_text(messages: list[Any]) -> str:
    for msg in reversed(messages):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role != "user":
            continue
        content = (
            msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
        )
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                b = block if isinstance(block, dict) else block.model_dump()
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
            return "\n".join(parts)
    return ""


def _concat_message_text(messages: list[Any], system: Any = None) -> str:
    """Concatenate all text from messages and system prompt."""
    parts: list[str] = []
    if system:
        if isinstance(system, str):
            parts.append(system)
        elif isinstance(system, list):
            for b in system:
                t = b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "")
                if t:
                    parts.append(t)
    for msg in messages:
        content = (
            msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
        )
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                b = block if isinstance(block, dict) else block.model_dump()
                for key in ("text", "thinking", "content"):
                    val = b.get(key, "")
                    if isinstance(val, str) and val:
                        parts.append(val)
    return "\n".join(parts)


def _estimate_tokens(messages: list[Any], system: Any = None) -> int:
    text = _concat_message_text(messages, system)
    if model_manager.is_loaded:
        return model_manager._count_tokens(text)
    return max(1, len(text) // 4)


def classify(request: Any, model_loaded: bool) -> tuple[str, str, str]:
    """Returns (route, reason, trigger).

    Default is LOCAL. Only forward when hard guards or forward_score >= threshold.
    """
    messages = (
        request.get("messages")
        if isinstance(request, dict)
        else getattr(request, "messages", [])
    )
    system = (
        request.get("system")
        if isinstance(request, dict)
        else getattr(request, "system", None)
    )

    latest = _get_latest_user_text(messages)

    # 1. Hard overrides
    if latest.lstrip().startswith("@cloud"):
        return Route.FORWARD, "user override @cloud", ""

    if latest.lstrip().startswith("@local"):
        return Route.LOCAL, "user override @local", ""

    # 2. Model availability (fail-open)
    if not model_loaded:
        return Route.FORWARD, "local model not loaded", ""

    # 3. Thinking requested — local model can't provide extended reasoning
    thinking = (
        request.get("thinking")
        if isinstance(request, dict)
        else getattr(request, "thinking", None)
    )
    if thinking:
        budget = (
            thinking.get("budget_tokens")
            if isinstance(thinking, dict)
            else getattr(thinking, "budget_tokens", None)
        )
        if budget and budget > 0:
            return Route.FORWARD, f"thinking requested (budget={budget})", ""

    # 4. Context size
    est_tokens = _estimate_tokens(messages, system)
    if est_tokens > config.max_local_context_tokens:
        return Route.FORWARD, f"context too large ({est_tokens} est. tokens)", ""

    # 5. Forward scoring — default is LOCAL, only forward on high forward_score
    tools = (
        request.get("tools")
        if isinstance(request, dict)
        else getattr(request, "tools", None)
    )
    num_tools = len(tools) if tools else 0
    fwd_score, reasons, trigger = _score_forward(latest, messages, num_tools=num_tools)
    threshold = _adaptive_threshold()
    reason_str = ", ".join(reasons)
    if threshold != FORWARD_THRESHOLD:
        reason_str += f", adaptive_t={threshold:.2f}"

    if fwd_score >= threshold:
        return Route.FORWARD, f"fwd={fwd_score:.2f} [{reason_str}]", trigger
    else:
        return Route.LOCAL, f"fwd={fwd_score:.2f} [{reason_str}]", trigger


def strip_routing_prefix(text: str) -> str:
    for prefix in ("@cloud", "@local"):
        if text.lstrip().startswith(prefix):
            return text.lstrip()[len(prefix) :].lstrip()
    return text
