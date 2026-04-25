from __future__ import annotations

from mlx_task_router.tool_format import (
    anthropic_messages_to_chat,
    anthropic_tools_to_openai,
    build_anthropic_content,
    parse_model_response,
)


# ---------------------------------------------------------------------------
# anthropic_tools_to_openai
# ---------------------------------------------------------------------------


class TestToolConversion:
    def test_basic_conversion(self, sample_tools):
        result = anthropic_tools_to_openai(sample_tools)
        assert len(result) == 1
        func = result[0]
        assert func["type"] == "function"
        assert func["function"]["name"] == "Bash"
        assert "command" in func["function"]["parameters"]["properties"]

    def test_preserves_description(self, sample_tools):
        result = anthropic_tools_to_openai(sample_tools)
        assert result[0]["function"]["description"] == "Run shell commands"

    def test_missing_description(self):
        tools = [{"name": "Foo", "input_schema": {"type": "object"}}]
        result = anthropic_tools_to_openai(tools)
        assert result[0]["function"]["description"] == ""

    def test_empty_tools(self):
        assert anthropic_tools_to_openai([]) == []

    def test_multiple_tools(self):
        tools = [
            {"name": "A", "description": "a", "input_schema": {"type": "object"}},
            {"name": "B", "description": "b", "input_schema": {"type": "object"}},
        ]
        result = anthropic_tools_to_openai(tools)
        assert len(result) == 2
        assert result[0]["function"]["name"] == "A"
        assert result[1]["function"]["name"] == "B"


# ---------------------------------------------------------------------------
# anthropic_messages_to_chat
# ---------------------------------------------------------------------------


class TestMessageConversion:
    def test_simple_user_message(self):
        msgs = [{"role": "user", "content": "hello"}]
        chat = anthropic_messages_to_chat(msgs)
        assert chat == [{"role": "user", "content": "hello"}]

    def test_system_string(self):
        msgs = [{"role": "user", "content": "hi"}]
        chat = anthropic_messages_to_chat(msgs, system="You are helpful")
        assert chat[0] == {"role": "system", "content": "You are helpful"}
        assert chat[1]["role"] == "user"

    def test_system_list(self):
        msgs = [{"role": "user", "content": "hi"}]
        system = [{"type": "text", "text": "Be concise"}]
        chat = anthropic_messages_to_chat(msgs, system=system)
        assert chat[0]["content"] == "Be concise"

    def test_user_content_blocks(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "text", "text": "world"},
                ],
            }
        ]
        chat = anthropic_messages_to_chat(msgs)
        assert chat[0]["content"] == "hello\nworld"

    def test_assistant_string(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        chat = anthropic_messages_to_chat(msgs)
        assert chat[1] == {"role": "assistant", "content": "hello"}

    def test_tool_result_conversion(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_123",
                        "content": "command output",
                    }
                ],
            }
        ]
        chat = anthropic_messages_to_chat(msgs)
        assert chat[0]["role"] == "tool"
        assert chat[0]["tool_call_id"] == "tool_123"
        assert chat[0]["content"] == "command output"

    def test_assistant_tool_use(self):
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Running command"},
                    {
                        "type": "tool_use",
                        "id": "tool_abc",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    },
                ],
            }
        ]
        chat = anthropic_messages_to_chat(msgs)
        assert chat[0]["content"] == "Running command"
        assert len(chat[0]["tool_calls"]) == 1
        tc = chat[0]["tool_calls"][0]
        assert tc["function"]["name"] == "Bash"

    def test_thinking_blocks_skipped(self):
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "hmm"},
                    {"type": "text", "text": "answer"},
                ],
            }
        ]
        chat = anthropic_messages_to_chat(msgs)
        assert chat[0]["content"] == "answer"
        assert "tool_calls" not in chat[0]


# ---------------------------------------------------------------------------
# parse_model_response
# ---------------------------------------------------------------------------


class TestParseModelResponse:
    def test_plain_text(self):
        text, calls = parse_model_response("Hello world")
        assert text == "Hello world"
        assert calls == []

    def test_tool_call(self):
        raw = 'Some text\n<tool_call>\n{"name": "Bash", "arguments": {"command": "ls"}}\n</tool_call>'
        text, calls = parse_model_response(raw)
        assert text == "Some text"
        assert len(calls) == 1
        assert calls[0]["name"] == "Bash"
        assert calls[0]["input"] == {"command": "ls"}
        assert calls[0]["id"].startswith("toolu_")

    def test_think_block_removed(self):
        raw = "<think>internal thought</think>Final answer"
        text, calls = parse_model_response(raw)
        assert text == "Final answer"
        assert "think" not in text

    def test_multiple_tool_calls(self):
        raw = (
            '<tool_call>\n{"name": "Bash", "arguments": {"command": "ls"}}\n</tool_call>\n'
            '<tool_call>\n{"name": "Read", "arguments": {"path": "/tmp"}}\n</tool_call>'
        )
        text, calls = parse_model_response(raw)
        assert len(calls) == 2
        assert calls[0]["name"] == "Bash"
        assert calls[1]["name"] == "Read"

    def test_invalid_json_skipped(self):
        raw = "<tool_call>not json</tool_call>Hello"
        text, calls = parse_model_response(raw)
        assert calls == []
        assert text == "Hello"

    def test_missing_name_skipped(self):
        raw = '<tool_call>{"arguments": {"x": 1}}</tool_call>Ok'
        text, calls = parse_model_response(raw)
        assert calls == []

    def test_tool_only_no_text(self):
        raw = '<tool_call>\n{"name": "Bash", "arguments": {"command": "pwd"}}\n</tool_call>'
        text, calls = parse_model_response(raw)
        assert text == ""
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# build_anthropic_content
# ---------------------------------------------------------------------------


class TestBuildAnthropicContent:
    def test_text_only(self):
        content = build_anthropic_content("hello", [])
        assert content == [{"type": "text", "text": "hello"}]

    def test_tool_calls_only(self):
        calls = [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]
        content = build_anthropic_content("", calls)
        assert len(content) == 1
        assert content[0]["type"] == "tool_use"

    def test_text_and_tools(self):
        calls = [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]
        content = build_anthropic_content("output", calls)
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "tool_use"

    def test_empty_returns_empty_text(self):
        content = build_anthropic_content("", [])
        assert content == [{"type": "text", "text": ""}]
