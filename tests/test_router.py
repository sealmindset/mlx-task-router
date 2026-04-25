from __future__ import annotations

from unittest.mock import patch

import pytest

from mlx_task_router.router import (
    Route,
    ROUTING_THRESHOLD,
    _estimate_tokens,
    _extract_executable_candidates,
    _first_meaningful_word,
    _get_latest_user_text,
    _score_request,
    classify,
    strip_routing_prefix,
)


# ---------------------------------------------------------------------------
# strip_routing_prefix
# ---------------------------------------------------------------------------


class TestStripRoutingPrefix:
    def test_cloud_prefix(self):
        assert strip_routing_prefix("@cloud explain this") == "explain this"

    def test_local_prefix(self):
        assert strip_routing_prefix("@local git status") == "git status"

    def test_no_prefix(self):
        assert strip_routing_prefix("git status") == "git status"

    def test_prefix_with_leading_whitespace(self):
        assert strip_routing_prefix("  @cloud help") == "help"

    def test_empty_after_prefix(self):
        assert strip_routing_prefix("@cloud") == ""


# ---------------------------------------------------------------------------
# _first_meaningful_word
# ---------------------------------------------------------------------------


class TestFirstMeaningfulWord:
    def test_executable_first(self):
        assert _first_meaningful_word("git status") == "git"

    def test_skips_ignored_words(self):
        assert _first_meaningful_word("the git status") == "git"

    def test_skips_single_char(self):
        assert _first_meaningful_word("a b git") == "git"

    def test_empty(self):
        assert _first_meaningful_word("") is None


# ---------------------------------------------------------------------------
# _extract_executable_candidates
# ---------------------------------------------------------------------------


class TestExtractCandidates:
    def test_filters_ignored(self):
        candidates = _extract_executable_candidates("the quick ls command")
        assert "the" not in candidates
        assert "ls" in candidates

    def test_filters_short(self):
        candidates = _extract_executable_candidates("a b cd ef")
        assert "a" not in candidates
        assert "cd" in candidates


# ---------------------------------------------------------------------------
# _score_request
# ---------------------------------------------------------------------------


class TestScoreRequest:
    @patch("mlx_task_router.router._is_executable", return_value=True)
    def test_executable_first_word_high_score(self, mock_exec):
        score, reasons, trigger = _score_request("git status")
        assert score >= 0.6
        assert any("first" in r for r in reasons)
        assert trigger.startswith("exec:")

    @patch("mlx_task_router.router._is_executable", return_value=False)
    def test_no_executable_no_exec_score(self, mock_exec):
        score, reasons, trigger = _score_request("hello world")
        assert not any("exec:" in r for r in reasons)

    def test_action_phrase_commit_push(self):
        score, reasons, trigger = _score_request("commit and push")
        assert score >= 0.5
        assert trigger.startswith("action:")

    def test_action_phrase_run_tests(self):
        score, reasons, trigger = _score_request("run the tests")
        assert score >= 0.5

    def test_action_phrase_install_deps(self):
        score, reasons, trigger = _score_request("install the dependencies")
        assert score >= 0.5

    def test_complexity_reduces_score(self):
        score, reasons, _ = _score_request("explain how the routing works")
        assert any("complex" in r for r in reasons)
        assert score < 0.0

    def test_question_mark_penalty(self):
        score, reasons, _ = _score_request("what is this?")
        assert any("question" in r for r in reasons)

    def test_short_message_bonus(self):
        score, reasons, _ = _score_request("ls")
        assert any("short" in r for r in reasons)

    def test_long_message_penalty(self):
        long_text = "x " * 150
        score, reasons, _ = _score_request(long_text)
        assert any("long" in r for r in reasons)

    def test_complexity_patterns(self):
        complex_msgs = [
            "refactor this module",
            "explain the code",
            "write a function for auth",
            "debug this bug",
            "optimize performance",
            "what do you think",
            "compare pros and cons",
        ]
        for msg in complex_msgs:
            score, reasons, _ = _score_request(msg)
            assert any("complex" in r for r in reasons), f"'{msg}' should trigger complexity"


# ---------------------------------------------------------------------------
# _get_latest_user_text
# ---------------------------------------------------------------------------


class TestGetLatestUserText:
    def test_string_content(self):
        msgs = [{"role": "user", "content": "hello"}]
        assert _get_latest_user_text(msgs) == "hello"

    def test_list_content(self):
        msgs = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "hello world"}],
            }
        ]
        assert _get_latest_user_text(msgs) == "hello world"

    def test_picks_last_user(self):
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]
        assert _get_latest_user_text(msgs) == "second"

    def test_empty(self):
        assert _get_latest_user_text([]) == ""

    def test_no_user_messages(self):
        msgs = [{"role": "assistant", "content": "hi"}]
        assert _get_latest_user_text(msgs) == ""


# ---------------------------------------------------------------------------
# _estimate_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_basic(self):
        msgs = [{"role": "user", "content": "a" * 400}]
        assert _estimate_tokens(msgs) == 100

    def test_with_system_string(self):
        msgs = [{"role": "user", "content": "hi"}]
        tokens = _estimate_tokens(msgs, system="a" * 800)
        assert tokens >= 200

    def test_with_system_list(self):
        msgs = [{"role": "user", "content": "hi"}]
        system = [{"type": "text", "text": "a" * 400}]
        tokens = _estimate_tokens(msgs, system=system)
        assert tokens >= 100

    def test_list_content(self):
        msgs = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "a" * 400}],
            }
        ]
        assert _estimate_tokens(msgs) == 100


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


class TestClassify:
    def test_cloud_override(self):
        req = {"messages": [{"role": "user", "content": "@cloud git status"}]}
        route, reason, trigger = classify(req, model_loaded=True)
        assert route == Route.FORWARD
        assert "override" in reason
        assert trigger == ""

    def test_local_override(self):
        req = {"messages": [{"role": "user", "content": "@local explain this"}]}
        route, reason, trigger = classify(req, model_loaded=True)
        assert route == Route.LOCAL
        assert "override" in reason

    def test_model_not_loaded_forwards(self):
        req = {"messages": [{"role": "user", "content": "git status"}]}
        route, reason, _ = classify(req, model_loaded=False)
        assert route == Route.FORWARD
        assert "not loaded" in reason

    def test_context_too_large_forwards(self):
        huge = "x " * 200_000
        req = {"messages": [{"role": "user", "content": huge}]}
        with patch("mlx_task_router.router.config") as mock_cfg:
            mock_cfg.max_local_context_tokens = 1000
            mock_cfg.routing_threshold = 0.5
            route, reason, _ = classify(req, model_loaded=True)
        assert route == Route.FORWARD
        assert "too large" in reason

    @patch("mlx_task_router.router._is_executable", return_value=True)
    def test_high_score_routes_local(self, mock_exec):
        req = {"messages": [{"role": "user", "content": "git status"}]}
        route, reason, trigger = classify(req, model_loaded=True)
        assert route == Route.LOCAL
        assert "score=" in reason

    def test_complex_request_forwards(self):
        req = {
            "messages": [
                {"role": "user", "content": "explain how the authentication system works in detail"}
            ]
        }
        route, reason, _ = classify(req, model_loaded=True)
        assert route == Route.FORWARD

    def test_action_phrase_routes_local(self):
        req = {"messages": [{"role": "user", "content": "commit and push"}]}
        route, reason, trigger = classify(req, model_loaded=True)
        assert route == Route.LOCAL
        assert trigger.startswith("action:")
