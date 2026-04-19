"""Convert between Anthropic and OpenAI/Qwen tool-calling formats.

Anthropic sends tool definitions with `input_schema` and expects `tool_use`
content blocks in responses.  Qwen/OpenAI models use `parameters` in tool
definitions and emit `<tool_call>` XML blocks.  This module bridges the two.
"""

from __future__ import annotations

import json
import random
import re
import string
from typing import Any

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


def _generate_tool_id() -> str:
    chars = string.ascii_lowercase + string.digits
    suffix = "".join(random.choices(chars, k=24))
    return f"toolu_{suffix}"


# --- Tool definition conversion ---


def anthropic_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted = []
    for tool in tools:
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            }
        )
    return converted


# --- Message conversion ---


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif hasattr(block, "type") and block.type == "text":
                parts.append(block.text)
        return "\n".join(parts)
    return str(content)


def anthropic_messages_to_chat(
    messages: list[dict[str, Any]],
    system: Any = None,
) -> list[dict[str, Any]]:
    chat: list[dict[str, Any]] = []

    if system:
        if isinstance(system, str):
            chat.append({"role": "system", "content": system})
        elif isinstance(system, list):
            text = "\n".join(
                b.get("text", "") if isinstance(b, dict) else b.text
                for b in system
                if (isinstance(b, dict) and b.get("type") == "text")
                or (hasattr(b, "type") and b.type == "text")
            )
            if text:
                chat.append({"role": "system", "content": text})

    for msg in messages:
        role = msg.get("role") if isinstance(msg, dict) else msg.role
        content = msg.get("content") if isinstance(msg, dict) else msg.content

        if role == "user":
            if isinstance(content, str):
                chat.append({"role": "user", "content": content})
                continue

            text_parts: list[str] = []
            tool_results: list[dict[str, Any]] = []

            for block in content:
                b = block if isinstance(block, dict) else block.model_dump()
                btype = b.get("type")

                if btype == "text":
                    text_parts.append(b.get("text", ""))
                elif btype == "tool_result":
                    rc = b.get("content", "")
                    if isinstance(rc, list):
                        rc = "\n".join(
                            x.get("text", "") if isinstance(x, dict) else str(x)
                            for x in rc
                        )
                    elif not isinstance(rc, str):
                        rc = str(rc)
                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": b.get("tool_use_id", ""),
                            "name": "",
                            "content": rc,
                        }
                    )

            for tr in tool_results:
                chat.append(tr)
            if text_parts:
                chat.append({"role": "user", "content": "\n".join(text_parts)})

        elif role == "assistant":
            if isinstance(content, str):
                chat.append({"role": "assistant", "content": content})
                continue

            text_parts_a: list[str] = []
            tool_calls: list[dict[str, Any]] = []

            for block in content:
                b = block if isinstance(block, dict) else block.model_dump()
                btype = b.get("type")

                if btype == "text":
                    text_parts_a.append(b.get("text", ""))
                elif btype == "tool_use":
                    tool_calls.append(
                        {
                            "id": b.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": b.get("name", ""),
                                "arguments": json.dumps(b.get("input", {})),
                            },
                        }
                    )
                # skip thinking blocks

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": "\n".join(text_parts_a) if text_parts_a else "",
            }
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            chat.append(assistant_msg)

    return chat


# --- Response parsing ---


def parse_model_response(text: str) -> tuple[str, list[dict[str, Any]]]:
    cleaned = THINK_RE.sub("", text).strip()

    tool_calls: list[dict[str, Any]] = []
    for match in TOOL_CALL_RE.finditer(cleaned):
        raw = match.group(1)
        try:
            call = json.loads(raw)
        except json.JSONDecodeError:
            continue

        name = call.get("name", "")
        arguments = call.get("arguments", {})
        if not name:
            continue

        tool_calls.append(
            {
                "type": "tool_use",
                "id": _generate_tool_id(),
                "name": name,
                "input": arguments if isinstance(arguments, dict) else {},
            }
        )

    remaining_text = TOOL_CALL_RE.sub("", cleaned).strip()
    return remaining_text, tool_calls


def build_anthropic_content(
    text: str, tool_calls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    content.extend(tool_calls)
    if not content:
        content.append({"type": "text", "text": ""})
    return content
