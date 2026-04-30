"""Tests for QA Gate — confidence-gated routing with shadow validation."""

import os
import json

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from mlx_task_router.qa_gate import QAGate, GateResult, _VALIDATION_PROMPT


class TestQAGate:
    def _make_gate(self) -> QAGate:
        return QAGate()

    def test_extract_request_string(self):
        gate = self._make_gate()
        msgs = [{"role": "user", "content": "hello world"}]
        assert gate._extract_request(msgs) == "hello world"

    def test_extract_request_blocks(self):
        gate = self._make_gate()
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "part 1"},
            {"type": "text", "text": "part 2"},
        ]}]
        assert "part 1" in gate._extract_request(msgs)
        assert "part 2" in gate._extract_request(msgs)

    def test_extract_request_empty(self):
        gate = self._make_gate()
        msgs = [{"role": "assistant", "content": "I can help"}]
        assert gate._extract_request(msgs) == ""

    def test_extract_request_last_user(self):
        gate = self._make_gate()
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "second"},
        ]
        assert gate._extract_request(msgs) == "second"

    def test_parse_json_plain(self):
        gate = self._make_gate()
        text = '{"equivalent": true, "confidence": 0.95, "category": "git_commands", "reason": "same"}'
        result = gate._parse_json(text)
        assert result["equivalent"] is True
        assert result["confidence"] == 0.95
        assert result["category"] == "git_commands"

    def test_parse_json_fenced(self):
        gate = self._make_gate()
        text = '```json\n{"equivalent": false, "confidence": 0.3, "category": "code_generation", "reason": "missing"}\n```'
        result = gate._parse_json(text)
        assert result["equivalent"] is False
        assert result["category"] == "code_generation"

    def test_parse_json_with_surrounding_text(self):
        gate = self._make_gate()
        text = 'Here is my analysis: {"equivalent": true, "confidence": 0.8, "category": "general", "reason": "ok"} end.'
        result = gate._parse_json(text)
        assert result["equivalent"] is True

    def test_parse_json_fallback(self):
        gate = self._make_gate()
        text = 'not json at all'
        result = gate._parse_json(text)
        assert result["equivalent"] is True  # Fail-open
        assert result["confidence"] == 0.0

    def test_gate_result_dataclass(self):
        gr = GateResult(
            equivalent=True,
            confidence=0.95,
            category="git_commands",
            reason="identical output",
            local_issues=[],
            local_response="git status",
            claude_response="git status",
        )
        assert gr.equivalent is True
        assert gr.category == "git_commands"
        assert gr.elapsed_ms == 0.0

    def test_validation_prompt_format(self):
        prompt = _VALIDATION_PROMPT.format(
            request="git status",
            local_response="On branch main",
            claude_response="On branch main",
        )
        assert "git status" in prompt
        assert "On branch main" in prompt
        assert "equivalent" in prompt

    def test_should_gate_delegates_to_trust(self):
        gate = self._make_gate()
        from unittest.mock import patch
        with patch("mlx_task_router.qa_gate.qa_trust") as mock_trust:
            mock_trust.should_gate.return_value = True
            assert gate.should_gate(0.5) is True
            mock_trust.should_gate.assert_called_once_with(0.5, None)

    def test_should_gate_with_category(self):
        gate = self._make_gate()
        from unittest.mock import patch
        with patch("mlx_task_router.qa_gate.qa_trust") as mock_trust:
            mock_trust.should_gate.return_value = False
            assert gate.should_gate(0.5, "git_commands") is False
            mock_trust.should_gate.assert_called_once_with(0.5, "git_commands")
