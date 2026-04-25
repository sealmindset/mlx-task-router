"""Tests for Pydantic models — especially content block validation.

Verifies that the proxy can parse all known Anthropic content block types
and gracefully handle unknown / future types via the catch-all generic model.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mlx_task_router.models import (
    ContentBlockGeneric,
    ContentBlockRedactedThinking,
    ContentBlockText,
    ContentBlockThinking,
    ContentBlockToolResult,
    ContentBlockToolUse,
    Message,
    MessagesRequest,
)


# ---------------------------------------------------------------------------
# ContentBlock union validation
# ---------------------------------------------------------------------------


class TestContentBlockParsing:
    """Ensure all Anthropic content block types parse without error."""

    def test_text_block(self):
        msg = Message(role="user", content=[{"type": "text", "text": "hello"}])
        assert msg.content[0].type == "text"

    def test_thinking_block(self):
        msg = Message(
            role="assistant",
            content=[
                {"type": "thinking", "thinking": "deep thoughts", "signature": "sig123"}
            ],
        )
        assert msg.content[0].type == "thinking"

    def test_redacted_thinking_block(self):
        msg = Message(
            role="assistant",
            content=[{"type": "redacted_thinking", "data": "encrypted_data_here"}],
        )
        block = msg.content[0]
        assert isinstance(block, ContentBlockRedactedThinking)
        assert block.data == "encrypted_data_here"

    def test_tool_use_block(self):
        msg = Message(
            role="assistant",
            content=[
                {
                    "type": "tool_use",
                    "id": "toolu_123",
                    "name": "bash",
                    "input": {"command": "ls"},
                }
            ],
        )
        assert msg.content[0].type == "tool_use"

    def test_tool_result_block(self):
        msg = Message(
            role="user",
            content=[
                {"type": "tool_result", "tool_use_id": "toolu_123", "content": "ok"}
            ],
        )
        assert msg.content[0].type == "tool_result"

    def test_unknown_block_type_uses_generic(self):
        """Future/unknown content types should fall through to ContentBlockGeneric."""
        msg = Message(
            role="assistant",
            content=[
                {"type": "server_tool_use", "id": "st_1", "name": "web_search", "input": {}}
            ],
        )
        block = msg.content[0]
        assert isinstance(block, ContentBlockGeneric)
        assert block.type == "server_tool_use"

    def test_document_block_uses_generic(self):
        msg = Message(
            role="user",
            content=[
                {"type": "document", "source": {"type": "base64", "data": "..."}}
            ],
        )
        block = msg.content[0]
        assert isinstance(block, ContentBlockGeneric)
        assert block.type == "document"


# ---------------------------------------------------------------------------
# Full request validation with mixed content
# ---------------------------------------------------------------------------


class TestMessagesRequestParsing:
    def test_simple_string_content(self):
        req = MessagesRequest(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": "hello"}],
        )
        assert len(req.messages) == 1

    def test_mixed_content_with_redacted_thinking(self):
        """Regression: conversations with extended thinking history must parse."""
        req = MessagesRequest(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[
                {"role": "user", "content": "help me fix this bug"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "analyzing...", "signature": "s1"},
                        {"type": "text", "text": "I see the issue"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "done"},
                        {"type": "text", "text": "what next?"},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "redacted_thinking", "data": "abc123"},
                        {"type": "text", "text": "Let me continue..."},
                    ],
                },
                {"role": "user", "content": "thanks"},
            ],
        )
        assert len(req.messages) == 5

    def test_completely_unknown_content_types(self):
        """Even brand-new content types from future API versions should parse."""
        req = MessagesRequest(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[
                {
                    "role": "assistant",
                    "content": [
                        {"type": "citations", "references": [{"uri": "https://example.com"}]},
                        {"type": "text", "text": "According to..."},
                    ],
                },
                {"role": "user", "content": "thanks"},
            ],
        )
        assert len(req.messages) == 2

    def test_extra_top_level_fields_ignored(self):
        """Extra fields like service_tier should not cause validation failure."""
        data = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "hi"}],
            "service_tier": "scale",
            "some_future_field": True,
        }
        req = MessagesRequest(**data)
        assert req.model == "claude-sonnet-4-20250514"
