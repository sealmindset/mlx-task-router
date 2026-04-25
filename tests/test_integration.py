"""Integration tests with real MLX model.

These tests are SKIPPED unless the MLX model is actually available.
Run with: pytest tests/test_integration.py -v
They require a GPU and ~48GB RAM for Qwen3-Coder-Next-4bit (or ~20GB for Qwen3-32B-4bit).
"""

from __future__ import annotations

import os
import pytest

try:
    import mlx
    import mlx_lm
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not MLX_AVAILABLE or os.getenv("SKIP_INTEGRATION", "1") == "1",
    reason="MLX not available or SKIP_INTEGRATION=1 (set SKIP_INTEGRATION=0 to run)",
)


@pytest.fixture(scope="module")
def loaded_model():
    """Load the real model once for all integration tests."""
    from mlx_task_router.local import ModelManager

    mm = ModelManager()
    model_name = os.getenv("MLX_MODEL", "mlx-community/Qwen3-32B-4bit")
    mm.load_model(model_name)
    yield mm
    mm.unload()


class TestModelLoading:
    def test_model_loads_successfully(self, loaded_model):
        assert loaded_model.is_loaded
        assert loaded_model.current_model is not None

    def test_token_counting(self, loaded_model):
        count = loaded_model._count_tokens("hello world")
        assert count > 0
        assert isinstance(count, int)


class TestGeneration:
    def test_simple_generation(self, loaded_model):
        from mlx_task_router.models import MessagesRequest

        request = MessagesRequest(
            model="claude-sonnet-4-20250514",
            max_tokens=50,
            messages=[{"role": "user", "content": "echo hello"}],
        )
        result = loaded_model.generate(request)

        assert result["type"] == "message"
        assert result["role"] == "assistant"
        assert isinstance(result["content"], list)
        assert len(result["content"]) > 0
        assert result["usage"]["input_tokens"] > 0
        assert result["usage"]["output_tokens"] > 0

    def test_response_has_valid_content_blocks(self, loaded_model):
        from mlx_task_router.models import MessagesRequest

        request = MessagesRequest(
            model="claude-sonnet-4-20250514",
            max_tokens=100,
            messages=[{"role": "user", "content": "What is 2+2?"}],
        )
        result = loaded_model.generate(request)

        for block in result["content"]:
            assert "type" in block
            assert block["type"] in ("text", "tool_use")
            if block["type"] == "text":
                assert "text" in block
                assert isinstance(block["text"], str)


class TestStreaming:
    def test_streaming_produces_sse_events(self, loaded_model):
        from mlx_task_router.models import MessagesRequest

        request = MessagesRequest(
            model="claude-sonnet-4-20250514",
            max_tokens=50,
            messages=[{"role": "user", "content": "echo hi"}],
            stream=True,
        )
        events = list(loaded_model.stream_generate(request))

        assert len(events) > 0
        event_text = "".join(events)
        assert "message_start" in event_text
        assert "message_stop" in event_text
        assert "content_block_start" in event_text
        assert "content_block_stop" in event_text

    def test_streaming_has_valid_json(self, loaded_model):
        import json

        from mlx_task_router.models import MessagesRequest

        request = MessagesRequest(
            model="claude-sonnet-4-20250514",
            max_tokens=50,
            messages=[{"role": "user", "content": "ls"}],
            stream=True,
        )
        events = list(loaded_model.stream_generate(request))

        for event in events:
            if "data: " in event:
                data_str = event.split("data: ", 1)[1].strip()
                parsed = json.loads(data_str)
                assert "type" in parsed


class TestToolCalling:
    def test_tool_use_with_bash(self, loaded_model):
        from mlx_task_router.models import MessagesRequest

        request = MessagesRequest(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            messages=[{"role": "user", "content": "Run: git status"}],
            tools=[{
                "name": "Bash",
                "description": "Run shell commands",
                "input_schema": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            }],
        )
        result = loaded_model.generate(request)

        assert result["type"] == "message"
        # Should either have text or tool_use blocks
        block_types = [b["type"] for b in result["content"]]
        assert any(t in ("text", "tool_use") for t in block_types)
