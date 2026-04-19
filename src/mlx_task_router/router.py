"""Classify incoming requests as LOCAL (MLX) or FORWARD (Anthropic API).

Routing uses confidence scoring instead of binary matching. Each signal
contributes a weighted score; the request routes locally only when the
total score meets the configurable threshold (default 0.5).

Score contributors:
  +0.6  executable detected as FIRST word (strong CLI intent)
  +0.3  executable detected elsewhere in message
  +0.5  CLI action phrase ("commit and push", "run the tests")
  +0.1  short message (<80 chars)
  -0.6  complexity pattern detected (explain, refactor, debug…)
  -0.1  question mark present
  -0.1  long message (>200 chars)

Hard overrides (bypass scoring):
  1. @cloud / @local prefix
  2. Local model not loaded → forward
  3. Context too large → forward
"""

from __future__ import annotations

import re
import shutil
from functools import lru_cache
from typing import Any

from mlx_task_router.config import config
from mlx_task_router.feedback import routing_feedback


class Route:
    LOCAL = "local"
    FORWARD = "forward"


ROUTING_THRESHOLD = config.routing_threshold

# --- Scoring weights ---
SCORE_EXEC_FIRST_WORD = 0.6
SCORE_EXEC_IN_TEXT = 0.3
SCORE_ACTION_PHRASE = 0.5
SCORE_SHORT_MSG = 0.1
SCORE_COMPLEXITY = -0.6
SCORE_QUESTION = -0.1
SCORE_LONG_MSG = -0.1

# ---------------------------------------------------------------------------
# Complexity signals
# ---------------------------------------------------------------------------
_COMPLEXITY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(explain|understand|walk\s+me\s+through|break\s+down|how\s+does|what\s+does|what\s+is|what\s+are|analyze|analyse)\b",
        r"\bwhy\s+(does|is|are|do|did|would|should|can't|doesn't|isn't)\b",
        r"\b(refactor|rewrite|redesign|architect|implement)\s+(a|an|the|this|new|my)\b",
        r"\b(write|develop|create)\s+(a|an|the|this|new|my)\s+(function|class|module|component|service|api|endpoint|test|script)\b",
        r"\b(fix|resolve|solve|troubleshoot|debug)\s+(this|the|a|an)\s+(bug|error|issue|problem|crash|failure|regression|leak)\b",
        r"\b(optimize|improve|enhance|speed\s+up|performance)\b",
        r"\b(scaffold|boilerplate|skeleton|stub)\b",
        r"\b(migrate|convert|port)\s+(to|from|the|this|between)\b",
        r"\b(plan|design|strategy|approach|architecture|proposal|roadmap)\b",
        r"\b(security\s+review|vulnerability|CVE|exploit|injection|XSS|CSRF|penetration\s+test)\b",
        r"\b(code\s+review|review\s+(the|this|my)\s+code|look\s+at\s+(the|this|my)\s+code)\b",
        r"\b(what\s+do\s+you\s+think|give\s+me\s+feedback|any\s+suggestions)\b",
        r"\b(write|create|draft)\s+(the\s+)?(docs|documentation|readme|docstring)\b",
        r"\b(compare|contrast|trade-?off|pros?\s+and\s+cons?|which\s+is\s+better|should\s+I|should\s+we|recommend)\b",
        r"\b(help\s+me|can\s+you|could\s+you|I\s+need|I\s+want)\s+(understand|figure\s+out|think\s+about|decide)\b",
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


def _score_request(text: str) -> tuple[float, list[str], str]:
    """Score a message for local routing confidence. Returns (score, reasons, trigger)."""
    score = 0.0
    reasons: list[str] = []
    trigger = ""

    # --- Positive signals ---

    # CLI action phrases (high confidence, no PATH lookup needed)
    for pattern in _CLI_ACTION_PHRASES:
        m = pattern.search(text)
        if m:
            score += SCORE_ACTION_PHRASE
            trigger = f"action:{m.group().lower()}"
            reasons.append(f"action:'{m.group()}' +{SCORE_ACTION_PHRASE}")
            break

    # Executable detection with position weighting
    first_word = _first_meaningful_word(text)
    candidates = _extract_executable_candidates(text)
    first_word_scored = False

    for candidate in candidates:
        if _is_executable(candidate):
            if candidate == first_word and not first_word_scored:
                score += SCORE_EXEC_FIRST_WORD
                reasons.append(f"exec:'{candidate}'(first) +{SCORE_EXEC_FIRST_WORD}")
                first_word_scored = True
            elif not first_word_scored or candidate != first_word:
                score += SCORE_EXEC_IN_TEXT
                reasons.append(f"exec:'{candidate}' +{SCORE_EXEC_IN_TEXT}")
            if not trigger:
                trigger = f"exec:{candidate}"
            break

    # Short message bonus
    if len(text) < 80:
        score += SCORE_SHORT_MSG
        reasons.append(f"short +{SCORE_SHORT_MSG}")

    # --- Negative signals ---

    # Complexity patterns
    for pattern in _COMPLEXITY_PATTERNS:
        m = pattern.search(text)
        if m:
            score += SCORE_COMPLEXITY
            reasons.append(f"complex:'{m.group()}' {SCORE_COMPLEXITY}")
            break

    # Question mark
    if "?" in text:
        score += SCORE_QUESTION
        reasons.append(f"question {SCORE_QUESTION}")

    # Long message
    if len(text) > 200:
        score += SCORE_LONG_MSG
        reasons.append(f"long {SCORE_LONG_MSG}")

    # --- Feedback penalty ---
    if trigger:
        penalty = routing_feedback.penalty(trigger)
        if penalty != 0.0:
            score += penalty
            reasons.append(f"feedback:{trigger} {penalty:.2f}")

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


def _estimate_tokens(messages: list[Any], system: Any = None) -> int:
    char_count = 0
    if system:
        if isinstance(system, str):
            char_count += len(system)
        elif isinstance(system, list):
            for b in system:
                t = b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "")
                char_count += len(t)
    for msg in messages:
        content = (
            msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
        )
        if isinstance(content, str):
            char_count += len(content)
        elif isinstance(content, list):
            for block in content:
                b = block if isinstance(block, dict) else block.model_dump()
                for key in ("text", "thinking", "content"):
                    val = b.get(key, "")
                    if isinstance(val, str):
                        char_count += len(val)
    return char_count // 4


def classify(request: Any, model_loaded: bool) -> tuple[str, str, str]:
    """Returns (route, reason, trigger). Trigger is non-empty only for scored routes."""
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

    # 1. Overrides
    if latest.lstrip().startswith("@cloud"):
        return Route.FORWARD, "user override @cloud", ""

    if latest.lstrip().startswith("@local"):
        return Route.LOCAL, "user override @local", ""

    # 2. Model availability
    if not model_loaded:
        return Route.FORWARD, "local model not loaded", ""

    # 3. Context size
    est_tokens = _estimate_tokens(messages, system)
    if est_tokens > config.max_local_context_tokens:
        return Route.FORWARD, f"context too large ({est_tokens} est. tokens)", ""

    # 4. Confidence scoring
    score, reasons, trigger = _score_request(latest)
    reason_str = ", ".join(reasons)

    if score >= ROUTING_THRESHOLD:
        return Route.LOCAL, f"score={score:.2f} [{reason_str}]", trigger
    else:
        return Route.FORWARD, f"score={score:.2f} [{reason_str}]", trigger


def strip_routing_prefix(text: str) -> str:
    for prefix in ("@cloud", "@local"):
        if text.lstrip().startswith(prefix):
            return text.lstrip()[len(prefix) :].lstrip()
    return text
