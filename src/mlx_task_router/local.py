"""MLX model manager with gear-shifting support."""

from __future__ import annotations

import gc
import json
import time
from typing import Any, Generator

from mlx_task_router.config import GearProfile, config
from mlx_task_router.tool_format import (
    anthropic_messages_to_chat,
    anthropic_tools_to_openai,
    build_anthropic_content,
    parse_model_response,
)

LOCAL_SYSTEM_PROMPT = """\
You are a CLI task assistant working within Claude Code. Your job is to execute \
command-line operations using the available tools. Be concise and direct.

Rules:
- Use the Bash tool to run shell commands
- Use Read to inspect files, Write to create/overwrite files, Edit to modify files
- For git operations, follow standard workflows (check status, stage, commit, push)
- Write clear, descriptive commit messages based on the actual diff
- Do NOT explain what you are about to do — just do it
- If a command fails, diagnose and retry once before reporting the error

Respond with tool calls using the <tool_call> format when you need to execute actions.\
"""


class ModelManager:
    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._current_gear: GearProfile | None = None
        self._loading = False

    @property
    def is_loaded(self) -> bool:
        return self._model is not None and self._tokenizer is not None

    @property
    def is_loading(self) -> bool:
        return self._loading

    @property
    def current_gear(self) -> GearProfile | None:
        return self._current_gear

    def load_gear(self, gear: GearProfile) -> None:
        from mlx_lm import load

        self._loading = True
        try:
            if self._model is not None:
                self._model = None
                self._tokenizer = None
                gc.collect()

            print(f"[gear] Loading {gear.name}: {gear.model}")
            t0 = time.time()
            self._model, self._tokenizer = load(gear.model)
            elapsed = time.time() - t0
            print(f"[gear] {gear.name} loaded in {elapsed:.1f}s")
            self._current_gear = gear
        finally:
            self._loading = False

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        self._current_gear = None
        gc.collect()

    def generate(
        self,
        request: Any,
        *,
        use_local_system_prompt: bool = True,
    ) -> dict[str, Any]:
        from mlx_lm import generate as mlx_generate

        messages = request.messages
        system = request.system
        tools = request.tools

        messages_raw = [
            m.model_dump() if hasattr(m, "model_dump") else m for m in messages
        ]

        system_raw = None
        if use_local_system_prompt:
            system_raw = LOCAL_SYSTEM_PROMPT
        elif system:
            if isinstance(system, str):
                system_raw = system
            elif isinstance(system, list):
                system_raw = "\n".join(
                    b.text if hasattr(b, "text") else b.get("text", "") for b in system
                )

        chat_messages = anthropic_messages_to_chat(messages_raw, system_raw)

        tools_openai = None
        if tools:
            tools_raw = [t.model_dump() if hasattr(t, "model_dump") else t for t in tools]
            tools_openai = anthropic_tools_to_openai(tools_raw)

        prompt = self._apply_chat_template(chat_messages, tools_openai)

        max_tokens = min(
            request.max_tokens,
            self._current_gear.max_tokens if self._current_gear else 8192,
        )

        response_text = mlx_generate(
            self._model,
            self._tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            verbose=False,
        )

        text, tool_calls = parse_model_response(response_text)
        content = build_anthropic_content(text, tool_calls)
        stop_reason = "tool_use" if tool_calls else "end_turn"

        input_tokens = self._count_tokens(prompt)
        output_tokens = self._count_tokens(response_text)

        return {
            "id": f"msg_local_{int(time.time() * 1000)}",
            "type": "message",
            "role": "assistant",
            "content": content,
            "model": request.model,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }

    def stream_generate(
        self,
        request: Any,
        *,
        use_local_system_prompt: bool = True,
    ) -> Generator[str, None, None]:
        from mlx_lm import stream_generate as mlx_stream

        messages = request.messages
        system = request.system
        tools = request.tools

        messages_raw = [
            m.model_dump() if hasattr(m, "model_dump") else m for m in messages
        ]

        system_raw = None
        if use_local_system_prompt:
            system_raw = LOCAL_SYSTEM_PROMPT
        elif system:
            if isinstance(system, str):
                system_raw = system
            elif isinstance(system, list):
                system_raw = "\n".join(
                    b.text if hasattr(b, "text") else b.get("text", "") for b in system
                )

        chat_messages = anthropic_messages_to_chat(messages_raw, system_raw)

        tools_openai = None
        if tools:
            tools_raw = [t.model_dump() if hasattr(t, "model_dump") else t for t in tools]
            tools_openai = anthropic_tools_to_openai(tools_raw)

        prompt = self._apply_chat_template(chat_messages, tools_openai)

        max_tokens = min(
            request.max_tokens,
            self._current_gear.max_tokens if self._current_gear else 8192,
        )

        input_tokens = self._count_tokens(prompt)
        response_id = f"msg_local_{int(time.time() * 1000)}"

        # Buffer the full response so we can parse tool calls
        full_text = ""
        for chunk in mlx_stream(
            self._model,
            self._tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
        ):
            full_text += chunk.text

        text, tool_calls = parse_model_response(full_text)
        content = build_anthropic_content(text, tool_calls)
        stop_reason = "tool_use" if tool_calls else "end_turn"
        output_tokens = self._count_tokens(full_text)

        # Emit SSE events in Anthropic format
        yield _sse(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": response_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": request.model,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            },
        )

        for idx, block in enumerate(content):
            btype = block.get("type")

            if btype == "text":
                yield _sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": idx,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
                # Stream text in chunks for a natural feel
                block_text = block.get("text", "")
                chunk_size = 12
                for i in range(0, len(block_text), chunk_size):
                    yield _sse(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": idx,
                            "delta": {
                                "type": "text_delta",
                                "text": block_text[i : i + chunk_size],
                            },
                        },
                    )
                yield _sse(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": idx},
                )

            elif btype == "tool_use":
                yield _sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": idx,
                        "content_block": {
                            "type": "tool_use",
                            "id": block["id"],
                            "name": block["name"],
                            "input": {},
                        },
                    },
                )
                input_json = json.dumps(block.get("input", {}))
                yield _sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": input_json,
                        },
                    },
                )
                yield _sse(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": idx},
                )

        yield _sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            },
        )

        yield _sse("message_stop", {"type": "message_stop"})

    def _apply_chat_template(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        try:
            kwargs: dict[str, Any] = {
                "add_generation_prompt": True,
                "tokenize": False,
            }
            if tools:
                kwargs["tools"] = tools
            # Disable built-in thinking for Qwen3 models
            kwargs["enable_thinking"] = False
            result = self._tokenizer.apply_chat_template(messages, **kwargs)
            if isinstance(result, str):
                return result
        except Exception:
            pass

        # Fallback: manual formatting
        prompt = ""
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            prompt += f"<|{role}|>\n{content}\n"
        prompt += "<|assistant|>\n"
        return prompt

    def _count_tokens(self, text: str) -> int:
        if not text:
            return 0
        try:
            encoded = self._tokenizer.encode(text)
            return len(encoded) if hasattr(encoded, "__len__") else len(list(encoded))
        except Exception:
            return max(1, len(text) // 4)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


model_manager = ModelManager()
