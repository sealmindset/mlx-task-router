"""Multi-model pool — manages fast (1.5B) and main (27B) models with tier routing.

When MLX_FAST_MODEL is configured, trivial requests (short CLI operations, simple
edits) are served by a small fast model at 5-10x the speed. The main model handles
coding, refactoring, and analysis. Complex tasks still forward to Claude Opus.

If MLX_FAST_MODEL is empty, all local requests go to the main model (backward compat).
"""

from __future__ import annotations

import gc
import threading
import time
from typing import Any, Generator

from mlx_task_router.config import config
from mlx_task_router.local import ModelManager, _build_sampling_args


class ModelPool:
    """Manages fast + main model slots with tier-based routing."""

    def __init__(self, main_manager: ModelManager):
        self._main = main_manager
        self._fast_model = None
        self._fast_tokenizer = None
        self._fast_sampler = None
        self._fast_logits_processors: list[Any] = []
        self._fast_model_name: str | None = None
        self._lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._main.is_loaded

    @property
    def fast_available(self) -> bool:
        return self._fast_model is not None and self._fast_tokenizer is not None

    @property
    def fast_model_name(self) -> str | None:
        return self._fast_model_name

    def load_fast_model(self) -> None:
        """Load the fast model if configured."""
        if not config.fast_model:
            return
        try:
            from mlx_lm import load

            print(f"[pool] Loading fast model: {config.fast_model}")
            t0 = time.time()
            self._fast_model, self._fast_tokenizer = load(config.fast_model)
            elapsed = time.time() - t0
            self._fast_model_name = config.fast_model
            print(f"[pool] Fast model loaded in {elapsed:.1f}s")

            # Build sampler for fast model (uses same sampling params)
            self._fast_sampler, self._fast_logits_processors = _build_sampling_args()
        except Exception as e:
            print(f"[pool] Fast model failed (non-fatal): {e}")
            self._fast_model = None
            self._fast_tokenizer = None

    def unload_fast(self) -> None:
        """Unload the fast model to free VRAM."""
        self._fast_model = None
        self._fast_tokenizer = None
        self._fast_sampler = None
        self._fast_logits_processors = []
        self._fast_model_name = None
        gc.collect()
        print("[pool] Fast model unloaded")

    def generate(self, request: Any, tier: str = "local") -> dict[str, Any]:
        """Generate with the appropriate model for the tier.

        tier="fast" uses the fast model (falls back to main if unavailable).
        tier="local" uses the main model.
        """
        if tier == "fast" and self.fast_available:
            return self._generate_fast(request)
        return self._main.generate(request)

    def stream_generate(self, request: Any, tier: str = "local") -> Generator[str, None, None]:
        """Stream generate with the appropriate model.

        tier="fast" uses the fast model (falls back to main if unavailable).
        tier="local" uses the main model.
        """
        if tier == "fast" and self.fast_available:
            yield from self._stream_generate_fast(request)
        else:
            yield from self._main.stream_generate(request)

    def _generate_fast(self, request: Any) -> dict[str, Any]:
        """Generate using the fast (small) model."""
        from mlx_lm import generate as mlx_generate
        from mlx_task_router.local import LOCAL_SYSTEM_PROMPT
        from mlx_task_router.tool_format import (
            anthropic_messages_to_chat,
            anthropic_tools_to_openai,
            build_anthropic_content,
            parse_model_response,
        )

        messages_raw = [
            m.model_dump() if hasattr(m, "model_dump") else m
            for m in request.messages
        ]
        chat_messages = anthropic_messages_to_chat(messages_raw, LOCAL_SYSTEM_PROMPT)

        tools_openai = None
        if request.tools:
            tools_raw = [t.model_dump() if hasattr(t, "model_dump") else t for t in request.tools]
            tools_openai = anthropic_tools_to_openai(tools_raw)

        prompt = self._fast_tokenizer.apply_chat_template(
            chat_messages,
            tools=tools_openai,
            add_generation_prompt=True,
        )
        if isinstance(prompt, list):
            prompt = self._fast_tokenizer.decode(prompt)

        max_tokens = min(request.max_tokens, config.fast_model_max_tokens)

        gen_kwargs: dict[str, Any] = {
            "prompt": prompt,
            "max_tokens": max_tokens,
            "verbose": False,
        }
        if self._fast_sampler is not None:
            gen_kwargs["sampler"] = self._fast_sampler
        if self._fast_logits_processors:
            gen_kwargs["logits_processors"] = self._fast_logits_processors

        with self._lock:
            response_text = mlx_generate(
                self._fast_model,
                self._fast_tokenizer,
                **gen_kwargs,
            )

        text, tool_calls = parse_model_response(response_text)
        content = build_anthropic_content(text, tool_calls)
        stop_reason = "tool_use" if tool_calls else "end_turn"

        input_tokens = max(1, len(prompt) // 4)
        output_tokens = max(1, len(response_text) // 4)

        return {
            "id": f"msg_fast_{int(time.time() * 1000)}",
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

    def _stream_generate_fast(self, request: Any) -> Generator[str, None, None]:
        """Stream generate using the fast model.

        Simplified streaming — no tool_call buffering (fast model handles trivial tasks).
        """
        from mlx_lm import stream_generate as mlx_stream
        from mlx_task_router.local import LOCAL_SYSTEM_PROMPT, _sse
        from mlx_task_router.tool_format import (
            anthropic_messages_to_chat,
            anthropic_tools_to_openai,
        )

        messages_raw = [
            m.model_dump() if hasattr(m, "model_dump") else m
            for m in request.messages
        ]
        chat_messages = anthropic_messages_to_chat(messages_raw, LOCAL_SYSTEM_PROMPT)

        tools_openai = None
        if request.tools:
            tools_raw = [t.model_dump() if hasattr(t, "model_dump") else t for t in request.tools]
            tools_openai = anthropic_tools_to_openai(tools_raw)

        prompt = self._fast_tokenizer.apply_chat_template(
            chat_messages,
            tools=tools_openai,
            add_generation_prompt=True,
        )
        if isinstance(prompt, list):
            prompt = self._fast_tokenizer.decode(prompt)

        max_tokens = min(request.max_tokens, config.fast_model_max_tokens)
        input_tokens = max(1, len(prompt) // 4)
        response_id = f"msg_fast_{int(time.time() * 1000)}"

        stream_kwargs: dict[str, Any] = {
            "prompt": prompt,
            "max_tokens": max_tokens,
        }
        if self._fast_sampler is not None:
            stream_kwargs["sampler"] = self._fast_sampler
        if self._fast_logits_processors:
            stream_kwargs["logits_processors"] = self._fast_logits_processors

        # Emit message_start
        yield _sse("message_start", {
            "type": "message_start",
            "message": {
                "id": response_id, "type": "message", "role": "assistant",
                "content": [], "model": request.model,
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": input_tokens, "output_tokens": 0},
            },
        })

        yield _sse("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""},
        })

        output_tokens = 0
        with self._lock:
            for chunk in mlx_stream(self._fast_model, self._fast_tokenizer, **stream_kwargs):
                token = chunk.text
                output_tokens += 1
                yield _sse("content_block_delta", {
                    "type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": token},
                })

        yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
        yield _sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        })
        yield _sse("message_stop", {"type": "message_stop"})

    def status(self) -> dict[str, Any]:
        """Status summary for the /pool endpoint."""
        return {
            "main_loaded": self._main.is_loaded,
            "main_model": self._main.current_model,
            "fast_available": self.fast_available,
            "fast_model": self._fast_model_name,
            "fast_configured": bool(config.fast_model),
        }


# Singleton — will be initialized in server.py lifespan
model_pool: ModelPool | None = None
