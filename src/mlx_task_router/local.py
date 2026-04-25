"""MLX model manager for local inference."""

from __future__ import annotations

import gc
import json
import time
from typing import Any, Generator

from mlx_task_router.config import config
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
        self._draft_model = None
        self._model_name: str | None = None
        self._loading = False
        self._system_prompt_cache: dict[str, str] = {}
        self._system_prompt_tokens_cache: dict[str, int] = {}

    @property
    def is_loaded(self) -> bool:
        return self._model is not None and self._tokenizer is not None

    @property
    def is_loading(self) -> bool:
        return self._loading

    @property
    def current_model(self) -> str | None:
        return self._model_name

    def load_model(self, model_name: str) -> None:
        from mlx_lm import load

        self._check_mlx_lm_version()
        self._loading = True
        try:
            if self._model is not None:
                self._model = None
                self._tokenizer = None
                gc.collect()

            print(f"[model] Loading {model_name}")
            t0 = time.time()
            self._model, self._tokenizer = load(model_name)
            elapsed = time.time() - t0
            print(f"[model] Loaded in {elapsed:.1f}s")
            self._model_name = model_name

            if config.draft_model:
                try:
                    print(f"[model] Loading draft model: {config.draft_model}")
                    t1 = time.time()
                    self._draft_model, _ = load(config.draft_model)
                    print(f"[model] Draft model loaded in {time.time() - t1:.1f}s")
                except Exception as e:
                    print(f"[model] Draft model failed (non-fatal): {e}")
                    self._draft_model = None

            self._warmup()
        finally:
            self._loading = False

    def _warmup(self) -> None:
        """Prime Metal shader cache with a short generation."""
        if not self.is_loaded:
            return
        try:
            from mlx_lm import generate as mlx_generate

            prompt = self._apply_chat_template(
                [{"role": "user", "content": "hi"}], None
            )
            t0 = time.time()
            mlx_generate(
                self._model, self._tokenizer, prompt=prompt, max_tokens=1, verbose=False,
            )
            print(f"[model] Warmup complete in {time.time() - t0:.1f}s")
        except Exception as e:
            print(f"[model] Warmup failed (non-fatal): {e}")

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        self._draft_model = None
        self._model_name = None
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
            config.model_max_tokens,
        )

        gen_kwargs: dict[str, Any] = {
            "prompt": prompt,
            "max_tokens": max_tokens,
            "verbose": False,
        }
        if config.temperature > 0:
            gen_kwargs["temp"] = config.temperature
        if config.top_p < 1.0:
            gen_kwargs["top_p"] = config.top_p
        if config.top_k > 0:
            gen_kwargs["top_k"] = config.top_k
        if config.repetition_penalty > 1.0:
            gen_kwargs["repetition_penalty"] = config.repetition_penalty
        if self._draft_model is not None:
            gen_kwargs["draft_model"] = self._draft_model
            gen_kwargs["num_draft_tokens"] = config.speculative_tokens

        response_text = mlx_generate(
            self._model,
            self._tokenizer,
            **gen_kwargs,
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
        """Stream tokens in real-time via SSE.

        Text tokens are emitted immediately as they arrive from MLX.
        When a <tool_call> tag is detected, we switch to buffering mode
        to collect the complete tool call before emitting it as a block.
        """
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
            config.model_max_tokens,
        )

        input_tokens = self._count_tokens(prompt)
        response_id = f"msg_local_{int(time.time() * 1000)}"

        stream_kwargs: dict[str, Any] = {
            "prompt": prompt,
            "max_tokens": max_tokens,
        }
        if config.temperature > 0:
            stream_kwargs["temp"] = config.temperature
        if config.top_p < 1.0:
            stream_kwargs["top_p"] = config.top_p
        if config.top_k > 0:
            stream_kwargs["top_k"] = config.top_k
        if config.repetition_penalty > 1.0:
            stream_kwargs["repetition_penalty"] = config.repetition_penalty

        # Emit message_start
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

        # Start text content block (index 0) — stream text tokens in real-time
        yield _sse(
            "content_block_start",
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "text", "text": ""}},
        )

        full_text = ""
        text_buffer = ""
        tool_buffer = ""
        in_tool_call = False
        tool_tag_prefix = "<tool_call>"
        tool_tag_end = "</tool_call>"
        output_tokens = 0

        for chunk in mlx_stream(
            self._model,
            self._tokenizer,
            **stream_kwargs,
        ):
            token = chunk.text
            full_text += token

            if in_tool_call:
                # Buffering tool call content
                tool_buffer += token
                if tool_tag_end in tool_buffer:
                    in_tool_call = False
                continue

            # Check if we're entering a tool_call tag
            text_buffer += token
            if "<tool_call>" in text_buffer:
                # Flush any text before the tag
                before_tag = text_buffer.split("<tool_call>", 1)[0]
                if before_tag:
                    yield _sse(
                        "content_block_delta",
                        {"type": "content_block_delta", "index": 0,
                         "delta": {"type": "text_delta", "text": before_tag}},
                    )
                in_tool_call = True
                tool_buffer = text_buffer.split("<tool_call>", 1)[1]
                text_buffer = ""
                if tool_tag_end in tool_buffer:
                    in_tool_call = False
                continue

            # Check for partial tag prefix at end of buffer (e.g. "<tool" or "<to")
            # to avoid emitting incomplete tags
            flush_up_to = len(text_buffer)
            for i in range(1, min(len(tool_tag_prefix) + 1, len(text_buffer) + 1)):
                suffix = text_buffer[-i:]
                if tool_tag_prefix.startswith(suffix):
                    flush_up_to = len(text_buffer) - i
                    break

            if flush_up_to > 0:
                to_emit = text_buffer[:flush_up_to]
                text_buffer = text_buffer[flush_up_to:]
                yield _sse(
                    "content_block_delta",
                    {"type": "content_block_delta", "index": 0,
                     "delta": {"type": "text_delta", "text": to_emit}},
                )

        # Flush remaining text buffer
        if text_buffer:
            yield _sse(
                "content_block_delta",
                {"type": "content_block_delta", "index": 0,
                 "delta": {"type": "text_delta", "text": text_buffer}},
            )

        # Close the text content block
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})

        # Parse tool calls from full response and emit as separate blocks
        _text, tool_calls = parse_model_response(full_text)
        stop_reason = "tool_use" if tool_calls else "end_turn"
        output_tokens = self._count_tokens(full_text)

        for idx_offset, tc in enumerate(tool_calls, start=1):
            yield _sse(
                "content_block_start",
                {"type": "content_block_start", "index": idx_offset,
                 "content_block": {"type": "tool_use", "id": tc["id"],
                                   "name": tc["name"], "input": {}}},
            )
            yield _sse(
                "content_block_delta",
                {"type": "content_block_delta", "index": idx_offset,
                 "delta": {"type": "input_json_delta",
                           "partial_json": json.dumps(tc.get("input", {}))}},
            )
            yield _sse("content_block_stop",
                        {"type": "content_block_stop", "index": idx_offset})

        yield _sse(
            "message_delta",
            {"type": "message_delta",
             "delta": {"stop_reason": stop_reason, "stop_sequence": None},
             "usage": {"output_tokens": output_tokens}},
        )
        yield _sse("message_stop", {"type": "message_stop"})

    def _apply_chat_template(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        # Check cache for identical system prompt to avoid re-encoding
        cache_key = self._make_template_cache_key(messages, tools)
        if cache_key and cache_key in self._system_prompt_cache:
            return self._system_prompt_cache[cache_key]

        result = self._render_chat_template(messages, tools)
        if result:
            if cache_key:
                self._system_prompt_cache[cache_key] = result
                self._system_prompt_tokens_cache[cache_key] = self._count_tokens(result)
            return result

        # Fallback: manual formatting
        prompt = ""
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            prompt += f"<|{role}|>\n{content}\n"
        prompt += "<|assistant|>\n"
        return prompt

    def _render_chat_template(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> str | None:
        try:
            kwargs: dict[str, Any] = {
                "add_generation_prompt": True,
                "tokenize": False,
            }
            if tools:
                kwargs["tools"] = tools
            # Qwen3-Coder-Next has no thinking mode; for older Qwen3 models
            # that support the kwarg, disable thinking to avoid <think> blocks.
            try:
                result = self._tokenizer.apply_chat_template(
                    messages, enable_thinking=False, **kwargs,
                )
            except TypeError:
                # Tokenizer does not support enable_thinking kwarg
                result = self._tokenizer.apply_chat_template(messages, **kwargs)
            if isinstance(result, str):
                return result
        except Exception:
            pass
        return None

    @staticmethod
    def _make_template_cache_key(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> str | None:
        """Create a cache key from the system prompt + tools signature.

        Returns None if there's no system message (nothing stable to cache).
        """
        import hashlib

        parts: list[str] = []
        for msg in messages:
            if msg.get("role") == "system":
                parts.append(msg.get("content", ""))
        if not parts:
            return None
        if tools:
            parts.append(json.dumps(tools, sort_keys=True))
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _check_mlx_lm_version() -> None:
        """Warn if mlx-lm is too old for MoE support."""
        try:
            import importlib.metadata
            version = importlib.metadata.version("mlx-lm")
            major, minor, patch = (int(x) for x in version.split(".")[:3])
            if (major, minor, patch) < (0, 30, 5):
                print(
                    f"[warn] mlx-lm {version} detected — Qwen3-Coder-Next requires "
                    f"mlx-lm >= 0.30.5 for MoE support. Please upgrade: "
                    f"pip install --upgrade mlx-lm"
                )
            else:
                print(f"[model] mlx-lm {version} — MoE support OK")
        except Exception:
            pass

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
