"""Classify incoming requests as LOCAL (MLX) or FORWARD (Anthropic API).

Routing rules, in priority order:
1. @cloud / @local prefix overrides in the latest user message
2. If the local model is not loaded, forward everything
3. If estimated context length exceeds MAX_LOCAL_CONTEXT_TOKENS, forward
4. Pattern-match the latest user message against CLI-task patterns
5. Default: forward to Anthropic
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from mlx_task_router.config import config


class Route(str, Enum):
    LOCAL = "local"
    FORWARD = "forward"


# Patterns that indicate mundane CLI / git / devops tasks
_LOCAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        # Git workflows
        r"\b(commit\s+(and\s+)?push|push\s+(to\s+)?(remote|origin|github|upstream))\b",
        r"\bgit\s+(commit|push|pull|fetch|stash|checkout|switch|branch|tag|log|status|diff|add|merge|rebase|reset|clean|clone|init|remote|cherry-pick|bisect|blame|show|revert|restore|am|format-patch)\b",
        r"\b(stage|unstage)\s+(all|files|changes|everything)\b",
        # GitHub CLI
        r"\bgh\s+(pr|issue|release|repo|auth|run|workflow|gist|label|project|secret|variable)\b",
        r"\b(create|open|close|merge|list|view|edit|review)\s+(a\s+)?(pr|pull\s*request|issue|release)\b",
        # Package managers
        r"\b(npm|yarn|pnpm|bun)\s+(install|add|remove|update|upgrade|run|build|test|start|init|publish|link|ci)\b",
        r"\b(pip|uv|pipx|poetry|pdm)\s+(install|sync|lock|add|remove|update|run|build|publish)\b",
        r"\b(cargo)\s+(build|run|test|check|clippy|fmt|add|remove|update|publish|bench)\b",
        r"\b(brew)\s+(install|uninstall|update|upgrade|list|search|info)\b",
        r"\b(gem|bundle)\s+(install|update|exec|add)\b",
        r"\b(go)\s+(build|run|test|get|mod|install|vet|fmt)\b",
        # Docker
        r"\b(docker|docker-compose|podman)\s+(build|run|stop|start|ps|logs|up|down|pull|push|exec|compose|rm|rmi|images|volume|network)\b",
        # Build systems
        r"\b(make|cmake|gradle|mvn|ant)\s+\w+",
        # Common CLI
        r"\b(mkdir|rmdir|touch|cp|mv|chmod|chown|ln)\s+",
        r"\b(curl|wget|httpie|http)\s+",
        r"\b(tar|zip|unzip|gzip|gunzip)\s+",
        # CI/CD
        r"\brun\s+(the\s+)?(tests|linter|formatter|build|ci|pipeline)\b",
        r"\b(lint|format|typecheck|type-check)\s+(the\s+)?(code|project|files|src)\b",
    ]
]

# Patterns that indicate complex tasks requiring real Claude
_COMPLEX_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b(explain|understand|why|how\s+does|what\s+does|what\s+is|analyze|review|audit|investigate|debug|diagnose)\b",
        r"\b(refactor|rewrite|redesign|architect|implement|build|create|add|write|develop)\s+(a|an|the|this|new)\b",
        r"\b(fix|resolve|solve|troubleshoot)\s+(this|the|a|an)\s+(bug|error|issue|problem|crash|failure)\b",
        r"\b(optimize|improve|enhance|performance|security)\b",
        r"\b(generate|scaffold|boilerplate|template|skeleton)\b",
        r"\b(migrate|convert|port|upgrade)\s+(to|from|the)\b",
        r"\b(plan|design|strategy|approach|architecture)\b",
    ]
]


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


def classify(request: Any, model_loaded: bool) -> tuple[Route, str]:
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

    if latest.lstrip().startswith("@cloud"):
        return Route.FORWARD, "user override @cloud"

    if latest.lstrip().startswith("@local"):
        return Route.LOCAL, "user override @local"

    if not model_loaded:
        return Route.FORWARD, "local model not loaded"

    est_tokens = _estimate_tokens(messages, system)
    if est_tokens > config.max_local_context_tokens:
        return Route.FORWARD, f"context too large ({est_tokens} est. tokens)"

    has_local = any(p.search(latest) for p in _LOCAL_PATTERNS)
    has_complex = any(p.search(latest) for p in _COMPLEX_PATTERNS)

    if has_local and not has_complex:
        return Route.LOCAL, "matched CLI task pattern"

    if has_local and has_complex:
        return Route.FORWARD, "mixed CLI + complex patterns — forwarding"

    return Route.FORWARD, "no local pattern match — default forward"


def strip_routing_prefix(text: str) -> str:
    for prefix in ("@cloud", "@local"):
        if text.lstrip().startswith(prefix):
            return text.lstrip()[len(prefix) :].lstrip()
    return text
