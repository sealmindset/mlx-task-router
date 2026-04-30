"""QA Gate — confidence-gated routing with pre-delivery shadow validation.

For requests in the uncertain zone (gate_lower ≤ forward_score < gate_upper),
generates both local and Claude responses in parallel, validates equivalence,
then delivers the appropriate one. Proven categories bypass the gate.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from mlx_task_router.config import config
from mlx_task_router.qa_trust import qa_trust


@dataclass
class GateResult:
    """Result of a gated validation."""

    equivalent: bool
    confidence: float
    category: str
    reason: str
    local_issues: list[str]
    local_response: str
    claude_response: str
    validation_tokens: int = 0
    elapsed_ms: float = 0.0


_VALIDATION_PROMPT = """You are validating whether a local AI model produced an equivalent response to a frontier model for a coding/development task.

ORIGINAL REQUEST:
{request}

LOCAL MODEL RESPONSE:
{local_response}

FRONTIER MODEL RESPONSE:
{claude_response}

Are these responses functionally equivalent? Consider:
1. Would the user achieve the same outcome from either response?
2. Is the code correct in both (if applicable)?
3. Are there any errors or omissions in the local response that the frontier model correctly handles?
4. Is the local response missing critical information present in the frontier response?

Return ONLY valid JSON (no markdown, no explanation outside JSON):
{{
  "equivalent": true or false,
  "confidence": 0.0 to 1.0,
  "reason": "brief explanation of why they are or aren't equivalent",
  "local_issues": ["list of specific problems in local response, empty if none"],
  "category": "one of: git_commands, shell_commands, simple_edits, code_generation, debugging, explanation, refactoring, multi_file, architecture, general"
}}"""


class QAGate:
    """Pre-delivery confidence gate with shadow validation."""

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=config.qa_gate_timeout + 5)
        return self._client

    async def shutdown(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def should_gate(self, forward_score: float, category_hint: str | None = None) -> bool:
        """Check if request should enter the gate."""
        return qa_trust.should_gate(forward_score, category_hint)

    async def validate(
        self,
        request_messages: list[dict],
        local_response: str,
        claude_response: str,
        forward_score: float,
    ) -> GateResult:
        """Validate local response against Claude response using validation model.

        Returns GateResult with equivalence judgment and category assignment.
        """
        t_start = time.time()

        # Extract request text for prompt
        request_text = self._extract_request(request_messages)

        prompt = _VALIDATION_PROMPT.format(
            request=request_text[:2000],
            local_response=local_response[:3000],
            claude_response=claude_response[:3000],
        )

        try:
            result_json = await self._call_validator(prompt)
            elapsed_ms = (time.time() - t_start) * 1000

            equivalent = result_json.get("equivalent", False)
            confidence = float(result_json.get("confidence", 0.5))
            category = result_json.get("category", "general")
            reason = result_json.get("reason", "")
            local_issues = result_json.get("local_issues", [])
            validation_tokens = result_json.get("_tokens", 0)

            gate_result = GateResult(
                equivalent=equivalent,
                confidence=confidence,
                category=category,
                reason=reason,
                local_issues=local_issues,
                local_response=local_response[:500],
                claude_response=claude_response[:500],
                validation_tokens=validation_tokens,
                elapsed_ms=elapsed_ms,
            )

            # Record in trust system
            avg_score = 5 if equivalent else 2
            qa_trust.record_outcome(
                category=category,
                passed=equivalent,
                score=avg_score,
                swapped=not equivalent,
                shadow_tokens=validation_tokens,
            )

            if config.log_routing:
                status = "PASS" if equivalent else "SWAP"
                print(
                    f"[qa-gate] {status} category={category} "
                    f"confidence={confidence:.2f} ({elapsed_ms:.0f}ms)"
                )

            return gate_result

        except Exception as e:
            elapsed_ms = (time.time() - t_start) * 1000
            if config.log_routing:
                print(f"[qa-gate] Validation error ({elapsed_ms:.0f}ms): {e}")
            # On error, deliver local (fail-open)
            return GateResult(
                equivalent=True,  # Assume equivalent on error (fail-open)
                confidence=0.0,
                category="unknown",
                reason=f"Validation error: {e}",
                local_issues=[],
                local_response=local_response[:500],
                claude_response=claude_response[:500],
                elapsed_ms=elapsed_ms,
            )

    async def _call_validator(self, prompt: str) -> dict:
        """Call the validation model API."""
        client = await self._ensure_client()

        headers = {
            "x-api-key": config.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        body = {
            "model": config.qa_gate_validation_model,
            "max_tokens": 512,
            "messages": [{"role": "user", "content": prompt}],
        }

        response = await client.post(
            f"{config.anthropic_api_url}/v1/messages",
            headers=headers,
            json=body,
            timeout=config.qa_gate_timeout,
        )
        response.raise_for_status()

        resp_json = response.json()
        text = ""
        for block in resp_json.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        # Extract token usage
        usage = resp_json.get("usage", {})
        tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

        # Parse JSON from response
        result = self._parse_json(text)
        result["_tokens"] = tokens
        return result

    async def generate_claude_response(
        self,
        messages: list[dict],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[str, int]:
        """Generate a Claude response for shadow comparison.

        Returns (response_text, tokens_used).
        """
        client = await self._ensure_client()

        headers = {
            "x-api-key": config.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        body = {
            "model": model or config.qa_gate_validation_model,
            "max_tokens": max_tokens or config.model_max_tokens,
            "messages": messages,
        }

        response = await client.post(
            f"{config.anthropic_api_url}/v1/messages",
            headers=headers,
            json=body,
            timeout=config.qa_gate_timeout,
        )
        response.raise_for_status()

        resp_json = response.json()
        text = ""
        for block in resp_json.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        usage = resp_json.get("usage", {})
        tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        return text, tokens

    def _extract_request(self, messages: list[dict]) -> str:
        """Extract the user request text from messages."""
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                return " ".join(parts)
        return ""

    def _parse_json(self, text: str) -> dict:
        """Parse JSON from model response, handling markdown fences."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]  # Remove opening fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON object in text
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
        return {"equivalent": True, "confidence": 0.0, "category": "unknown", "reason": "Parse error"}


# Module-level singleton
qa_gate = QAGate()
