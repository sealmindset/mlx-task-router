"""Tests for Trust-But-Verify engine (verify.py)."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mlx_task_router.verify import TBVEngine, VerificationResult, VerificationTask


@pytest.fixture
def engine():
    """Fresh TBV engine for each test."""
    e = TBVEngine()
    e._total_verified = 0
    e._total_passed = 0
    e._results = []
    e._recent_change_counter = 0
    return e


class TestAdaptiveSampling:
    """Tests for adaptive sampling rate logic."""

    def test_cold_start_rate(self, engine, monkeypatch):
        """Cold start (< 50 verified) returns 20%."""
        monkeypatch.setattr("mlx_task_router.verify.config.verify_enabled", True)
        monkeypatch.setattr("mlx_task_router.verify.config.verify_sample_rate", 0.0)
        engine._total_verified = 10
        assert engine.adaptive_sample_rate == 0.20

    def test_stable_rate(self, engine, monkeypatch):
        """High pass rate returns 5%."""
        monkeypatch.setattr("mlx_task_router.verify.config.verify_enabled", True)
        monkeypatch.setattr("mlx_task_router.verify.config.verify_sample_rate", 0.0)
        engine._total_verified = 100
        engine._total_passed = 95  # 95% pass rate
        assert engine.adaptive_sample_rate == 0.05

    def test_degrading_rate(self, engine, monkeypatch):
        """Low pass rate returns 15%."""
        monkeypatch.setattr("mlx_task_router.verify.config.verify_enabled", True)
        monkeypatch.setattr("mlx_task_router.verify.config.verify_sample_rate", 0.0)
        engine._total_verified = 100
        engine._total_passed = 80  # 80% pass rate
        assert engine.adaptive_sample_rate == 0.15

    def test_burst_after_change(self, engine, monkeypatch):
        """After routing change, rate bursts to 30%."""
        monkeypatch.setattr("mlx_task_router.verify.config.verify_enabled", True)
        monkeypatch.setattr("mlx_task_router.verify.config.verify_sample_rate", 0.0)
        engine._total_verified = 100
        engine._total_passed = 95
        engine.notify_routing_change()
        assert engine.adaptive_sample_rate == 0.30

    def test_manual_override(self, engine, monkeypatch):
        """Manual override takes precedence."""
        monkeypatch.setattr("mlx_task_router.verify.config.verify_enabled", True)
        monkeypatch.setattr("mlx_task_router.verify.config.verify_sample_rate", 0.5)
        assert engine.adaptive_sample_rate == 0.5

    def test_should_sample_disabled(self, engine, monkeypatch):
        """Returns False when TBV is disabled."""
        monkeypatch.setattr("mlx_task_router.verify.config.verify_enabled", False)
        assert engine.should_sample() is False

    def test_should_sample_enabled(self, engine, monkeypatch):
        """Returns True some of the time when enabled with high rate."""
        monkeypatch.setattr("mlx_task_router.verify.config.verify_enabled", True)
        monkeypatch.setattr("mlx_task_router.verify.config.verify_sample_rate", 1.0)
        assert engine.should_sample() is True

    def test_borderline_gets_higher_rate(self, engine, monkeypatch):
        """Borderline forwards get 2x rate."""
        monkeypatch.setattr("mlx_task_router.verify.config.verify_enabled", True)
        monkeypatch.setattr("mlx_task_router.verify.config.verify_sample_rate", 0.5)
        # With 0.5 base rate, borderline should get 1.0
        # Run 100 times, should always sample
        results = [engine.should_sample(forward_score=0.65, is_borderline=True) for _ in range(100)]
        assert all(results)  # 1.0 rate means always sample


class TestVerificationResult:
    """Tests for VerificationResult data structure."""

    def test_to_dict(self):
        """VerificationResult serializes correctly."""
        result = VerificationResult(
            timestamp=1234567890.0,
            request_hash="abc123",
            route="local",
            mode="async",
            strategy="local_check",
            scores={"correctness": 5, "completeness": 4, "code_quality": 4, "routing_appropriateness": 5},
            overall_pass=True,
            could_be_local=True,
            suggested_route="local",
            confidence=0.95,
        )
        d = result.to_dict()
        assert d["route"] == "local"
        assert d["overall_pass"] is True
        assert d["scores"]["correctness"] == 5

    def test_error_result(self):
        """Error results have empty scores."""
        result = VerificationResult(
            timestamp=1234567890.0,
            request_hash="xyz789",
            route="local",
            mode="async",
            strategy="local_check",
            error="Connection timeout",
        )
        d = result.to_dict()
        assert d["error"] == "Connection timeout"
        assert d["scores"] == {}


class TestVerificationTask:
    """Tests for VerificationTask structure."""

    def test_task_creation(self):
        """Task can be created with all fields."""
        task = VerificationTask(
            request_messages=[{"role": "user", "content": "git status"}],
            response_text="Here's the git status...",
            route="local",
            forward_score=0.2,
            trigger="exec:git",
            strategy="local_check",
        )
        assert task.route == "local"
        assert task.shadow_response is None
        assert task.priority == 0

    def test_shadow_task(self):
        """Shadow tasks include the reference response."""
        task = VerificationTask(
            request_messages=[{"role": "user", "content": "explain this code"}],
            response_text="Local explanation...",
            route="local",
            forward_score=0.4,
            trigger="complex:explain",
            shadow_response="Opus explanation...",
            strategy="local_check",
        )
        assert task.shadow_response == "Opus explanation..."


class TestBuildPrompt:
    """Tests for prompt building logic."""

    def test_local_check_prompt(self, engine, monkeypatch):
        """Local check prompt includes request and response."""
        monkeypatch.setattr("mlx_task_router.verify.config.verify_min_score", 3)
        task = VerificationTask(
            request_messages=[{"role": "user", "content": "Write hello world"}],
            response_text="print('hello world')",
            route="local",
            forward_score=0.1,
            trigger="",
        )
        prompt = engine._build_prompt(task)
        assert "Write hello world" in prompt
        assert "print('hello world')" in prompt
        assert "Correctness" in prompt

    def test_retroactive_prompt(self, engine, monkeypatch):
        """Retroactive prompt asks if local could have handled it."""
        monkeypatch.setattr("mlx_task_router.verify.config.verify_min_score", 3)
        task = VerificationTask(
            request_messages=[{"role": "user", "content": "Refactor entire module"}],
            response_text="Here's the refactored code...",
            route="forward",
            forward_score=0.8,
            trigger="complex:refactor",
            strategy="retroactive",
        )
        prompt = engine._build_prompt(task)
        assert "Could a competent 27B local model" in prompt
        assert "Refactor entire module" in prompt

    def test_shadow_prompt(self, engine, monkeypatch):
        """Shadow prompt includes both local and opus responses."""
        monkeypatch.setattr("mlx_task_router.verify.config.verify_min_score", 3)
        task = VerificationTask(
            request_messages=[{"role": "user", "content": "Fix this bug"}],
            response_text="Local fix...",
            route="local",
            forward_score=0.3,
            trigger="",
            shadow_response="Opus fix...",
        )
        prompt = engine._build_prompt(task)
        assert "Local fix..." in prompt
        assert "Opus fix..." in prompt
        assert "REFERENCE MODEL RESPONSE" in prompt


class TestPassRate:
    """Tests for pass rate calculation."""

    def test_empty_pass_rate(self, engine):
        """Empty history returns 1.0."""
        assert engine.pass_rate == 1.0

    def test_calculated_pass_rate(self, engine):
        """Pass rate calculated from totals."""
        engine._total_verified = 20
        engine._total_passed = 18
        assert engine.pass_rate == 0.9

    def test_zero_pass_rate(self, engine):
        """All failures returns 0.0."""
        engine._total_verified = 10
        engine._total_passed = 0
        assert engine.pass_rate == 0.0


class TestStatus:
    """Tests for status reporting."""

    def test_status_disabled(self, engine, monkeypatch):
        """Status when TBV is disabled."""
        monkeypatch.setattr("mlx_task_router.verify.config.verify_enabled", False)
        monkeypatch.setattr("mlx_task_router.verify.config.verify_shadow_mode", False)
        monkeypatch.setattr("mlx_task_router.verify.config.verify_sample_rate", 0.0)
        monkeypatch.setattr("mlx_task_router.verify.config.verify_queue_size", 50)
        monkeypatch.setattr("mlx_task_router.verify.config.verify_model", "claude-sonnet-4-20250514")
        monkeypatch.setattr("mlx_task_router.verify.config.verify_auto_tune", True)
        s = engine.status()
        assert s["enabled"] is False
        assert s["running"] is False
        assert s["total_verified"] == 0

    def test_status_with_results(self, engine, monkeypatch):
        """Status reflects accumulated results."""
        monkeypatch.setattr("mlx_task_router.verify.config.verify_enabled", True)
        monkeypatch.setattr("mlx_task_router.verify.config.verify_shadow_mode", False)
        monkeypatch.setattr("mlx_task_router.verify.config.verify_sample_rate", 0.0)
        monkeypatch.setattr("mlx_task_router.verify.config.verify_queue_size", 50)
        monkeypatch.setattr("mlx_task_router.verify.config.verify_model", "claude-sonnet-4-20250514")
        monkeypatch.setattr("mlx_task_router.verify.config.verify_auto_tune", True)
        engine._total_verified = 50
        engine._total_passed = 45
        s = engine.status()
        assert s["total_verified"] == 50
        assert s["pass_rate"] == 0.9


class TestReset:
    """Tests for reset functionality."""

    def test_reset_clears_data(self, engine):
        """Reset clears all verification data."""
        engine._total_verified = 100
        engine._total_passed = 90
        engine._results.append(
            VerificationResult(
                timestamp=time.time(), request_hash="x", route="local",
                mode="async", strategy="local_check",
            )
        )
        engine.reset()
        assert engine._total_verified == 0
        assert engine._total_passed == 0
        assert len(engine._results) == 0


class TestRecentResults:
    """Tests for recent results retrieval."""

    def test_recent_results_empty(self, engine):
        """Empty results list."""
        assert engine.recent_results() == []

    def test_recent_results_limited(self, engine):
        """Results respect limit param."""
        for i in range(10):
            engine._results.append(
                VerificationResult(
                    timestamp=time.time(), request_hash=f"h{i}", route="local",
                    mode="async", strategy="local_check",
                )
            )
        results = engine.recent_results(limit=5)
        assert len(results) == 5


class TestExtractRequestText:
    """Tests for request text extraction."""

    def test_simple_text(self, engine):
        """Extracts text from simple user message."""
        msgs = [{"role": "user", "content": "hello world"}]
        assert engine._extract_request_text(msgs) == "hello world"

    def test_multipart_text(self, engine):
        """Extracts text from multi-block user message."""
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ]}]
        assert engine._extract_request_text(msgs) == "first second"

    def test_last_user_message(self, engine):
        """Extracts from the last user message."""
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "response"},
            {"role": "user", "content": "second"},
        ]
        assert engine._extract_request_text(msgs) == "second"

    def test_empty_messages(self, engine):
        """Returns empty string for empty messages."""
        assert engine._extract_request_text([]) == ""


class TestEnqueue:
    """Tests for queue behavior."""

    @pytest.mark.asyncio
    async def test_enqueue_when_not_running(self, engine):
        """Enqueue fails when engine not started."""
        task = VerificationTask(
            request_messages=[], response_text="", route="local",
            forward_score=0.0, trigger="",
        )
        assert engine.enqueue(task) is False

    @pytest.mark.asyncio
    async def test_enqueue_when_running(self, engine, monkeypatch):
        """Enqueue succeeds when engine is started."""
        monkeypatch.setattr("mlx_task_router.verify.config.verify_queue_size", 10)
        await engine.start()
        try:
            task = VerificationTask(
                request_messages=[], response_text="", route="local",
                forward_score=0.0, trigger="",
            )
            assert engine.enqueue(task) is True
            assert engine._queue.qsize() == 1
        finally:
            await engine.stop()
