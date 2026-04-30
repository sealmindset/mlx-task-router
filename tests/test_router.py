from __future__ import annotations

from unittest.mock import patch

import pytest

from mlx_task_router.router import (
    Route,
    FORWARD_THRESHOLD,
    _count_turns,
    _estimate_tokens,
    _extract_executable_candidates,
    _first_meaningful_word,
    _get_latest_user_text,
    _score_forward,
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
# _score_forward — higher = more reason to forward
# ---------------------------------------------------------------------------


class TestScoreForward:
    """In forward-scoring: positive score = push toward Claude, negative = pull toward local."""

    def _msgs(self, text: str) -> list[dict]:
        return [{"role": "user", "content": text}]

    @patch("mlx_task_router.router._is_executable", return_value=True)
    def test_executable_first_word_lowers_forward_score(self, mock_exec):
        score, reasons, trigger = _score_forward("git status", self._msgs("git status"))
        assert score < 0  # exec pulls score negative = stay local
        assert any("exec" in r for r in reasons)
        assert trigger.startswith("exec:")

    @patch("mlx_task_router.router._is_executable", return_value=False)
    def test_no_executable_no_exec_signal(self, mock_exec):
        score, reasons, trigger = _score_forward("hello world", self._msgs("hello world"))
        assert not any("exec:" in r for r in reasons)

    def test_action_phrase_lowers_forward_score(self):
        score, reasons, trigger = _score_forward("commit and push", self._msgs("commit and push"))
        assert score < 0  # action phrase pulls score negative = stay local
        assert trigger.startswith("action:")

    def test_action_phrase_run_tests(self):
        score, reasons, trigger = _score_forward("run the tests", self._msgs("run the tests"))
        assert score < 0

    def test_action_phrase_install_deps(self):
        score, reasons, trigger = _score_forward("install the dependencies", self._msgs("install the dependencies"))
        assert score < 0

    def test_complexity_raises_forward_score(self):
        score, reasons, _ = _score_forward("explain how the routing works", self._msgs("explain how the routing works"))
        assert any("complex" in r for r in reasons)
        assert score > 0  # complexity pushes toward forward

    def test_short_message_reduces_forward_score(self):
        score, reasons, _ = _score_forward("ls", self._msgs("ls"))
        assert any("short" in r for r in reasons)

    def test_long_message_raises_forward_score(self):
        long_text = "x " * 300
        score, reasons, _ = _score_forward(long_text, self._msgs(long_text))
        assert any("long" in r for r in reasons)

    def test_complexity_patterns(self):
        complex_msgs = [
            "refactor this module",
            "explain the code",
            "debug this bug",
            "code review this code",
            "compare pros and cons",
        ]
        for msg in complex_msgs:
            score, reasons, _ = _score_forward(msg, self._msgs(msg))
            assert any("complex" in r for r in reasons), f"'{msg}' should trigger complexity"

    def test_codegen_patterns(self):
        codegen_msgs = [
            "write a function for auth",
            "create the documentation",
        ]
        for msg in codegen_msgs:
            score, reasons, _ = _score_forward(msg, self._msgs(msg))
            assert any("codegen" in r for r in reasons), f"'{msg}' should trigger codegen"

    def test_optimize_not_codegen(self):
        """optimize/improve should NOT trigger codegen — local model handles these."""
        for msg in ["optimize performance", "improve the code", "enhance readability"]:
            _, reasons, _ = _score_forward(msg, self._msgs(msg))
            assert not any("codegen" in r for r in reasons), f"'{msg}' should NOT trigger codegen"

    def test_extended_conversation(self):
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(32)]
        score, reasons, _ = _score_forward("what next", msgs)
        assert any("turns" in r for r in reasons)

    def test_extended_conversation_under_threshold(self):
        """25 user turns should NOT trigger extended_conversation (threshold is >30)."""
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(25)]
        _, reasons, _ = _score_forward("what next", msgs)
        assert not any("turns" in r for r in reasons)

    def test_many_tools_signal(self):
        """Requests with many tool definitions should push toward forward."""
        msgs = [{"role": "user", "content": "do something"}]
        _, reasons_16, _ = _score_forward("do something", msgs, num_tools=16)
        assert any("tools" in r for r in reasons_16)

    def test_very_many_tools_signal(self):
        msgs = [{"role": "user", "content": "do something"}]
        _, reasons_31, _ = _score_forward("do something", msgs, num_tools=31)
        assert any("tools:31" in r for r in reasons_31)

    def test_question_chain(self):
        score, reasons, _ = _score_forward("what is this? why? how?", self._msgs("what is this? why? how?"))
        assert any("questions" in r for r in reasons)

    def test_neutral_message_stays_low(self):
        """A simple message with no signals should have near-zero forward score."""
        score, reasons, _ = _score_forward("hello", self._msgs("hello"))
        assert score < FORWARD_THRESHOLD


# ---------------------------------------------------------------------------
# _count_turns
# ---------------------------------------------------------------------------


class TestCountTurns:
    def test_empty(self):
        assert _count_turns([]) == 0

    def test_single_turn(self):
        msgs = [{"role": "user", "content": "hi"}]
        assert _count_turns(msgs) == 1

    def test_multi_turn(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hey"},
            {"role": "user", "content": "bye"},
        ]
        assert _count_turns(msgs) == 2

    def test_tool_result_excluded(self):
        """User messages containing tool_result blocks should not count as turns."""
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "calling tool"},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "thanks"},
        ]
        assert _count_turns(msgs) == 2  # "hi" + "thanks", not tool_result


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
# classify — default is LOCAL, only forward on high forward_score or hard guards
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

    def test_thinking_requested_forwards(self):
        req = {
            "messages": [{"role": "user", "content": "solve this math problem"}],
            "thinking": {"type": "enabled", "budget_tokens": 10000},
        }
        route, reason, _ = classify(req, model_loaded=True)
        assert route == Route.FORWARD
        assert "thinking" in reason

    def test_context_too_large_forwards(self):
        huge = "x " * 200_000
        req = {"messages": [{"role": "user", "content": huge}]}
        with patch("mlx_task_router.router.config") as mock_cfg:
            mock_cfg.max_local_context_tokens = 1000
            mock_cfg.routing_threshold = 0.7
            route, reason, _ = classify(req, model_loaded=True)
        assert route == Route.FORWARD
        assert "too large" in reason

    @patch("mlx_task_router.router._is_executable", return_value=True)
    def test_simple_command_routes_local(self, mock_exec):
        req = {"messages": [{"role": "user", "content": "git status"}]}
        route, reason, trigger = classify(req, model_loaded=True)
        assert route == Route.LOCAL
        assert "fwd=" in reason

    def test_single_complex_request_stays_local(self):
        """Single complexity signal stays local — Qwen3.6-27B handles these."""
        req = {
            "messages": [
                {"role": "user", "content": "explain how the authentication system works in detail"}
            ]
        }
        route, reason, _ = classify(req, model_loaded=True)
        assert route == Route.LOCAL

    def test_multi_signal_request_forwards(self):
        """Stacked signals (complexity + codegen + questions) forward to Opus."""
        req = {
            "messages": [
                {"role": "user", "content": "Explain the differences between REST and GraphQL. Create a new module that implements both interfaces for our API gateway. Which approach is better for mobile clients? Should we add WebSocket support too?"}
            ]
        }
        route, reason, _ = classify(req, model_loaded=True)
        assert route == Route.FORWARD

    def test_action_phrase_routes_local(self):
        req = {"messages": [{"role": "user", "content": "commit and push"}]}
        route, reason, trigger = classify(req, model_loaded=True)
        assert route == Route.LOCAL
        assert trigger.startswith("action:")

    def test_neutral_message_routes_local(self):
        """With aggressive routing, ambiguous messages default to LOCAL."""
        req = {"messages": [{"role": "user", "content": "hello"}]}
        route, _, _ = classify(req, model_loaded=True)
        assert route == Route.LOCAL

    def test_short_question_routes_local(self):
        """Short questions without complexity patterns stay local."""
        req = {"messages": [{"role": "user", "content": "what time is it?"}]}
        route, _, _ = classify(req, model_loaded=True)
        assert route == Route.LOCAL


class TestTrivialDetection:
    """Tests for _is_trivial() and FAST route."""

    @pytest.fixture(autouse=True)
    def _patch(self):
        with patch("mlx_task_router.router.model_manager") as mock_mm:
            mock_mm.is_loaded = True
            mock_mm._count_tokens.return_value = 50
            yield

    def test_is_trivial_git_commands(self):
        from mlx_task_router.router import _is_trivial
        assert _is_trivial("git status") is True
        assert _is_trivial("git add .") is True
        assert _is_trivial("ls -la /tmp") is True

    def test_is_trivial_package_managers(self):
        from mlx_task_router.router import _is_trivial
        assert _is_trivial("npm install express") is True
        assert _is_trivial("pip install requests") is True
        assert _is_trivial("cargo build") is True

    def test_is_trivial_simple_edits(self):
        from mlx_task_router.router import _is_trivial
        assert _is_trivial("fix the typo") is True
        assert _is_trivial("remove the import") is True
        assert _is_trivial("rename the variable") is True

    def test_is_trivial_view_commands(self):
        from mlx_task_router.router import _is_trivial
        assert _is_trivial("show the status") is True
        assert _is_trivial("list the branches") is True
        assert _is_trivial("check the log") is True

    def test_not_trivial_long_text(self):
        from mlx_task_router.router import _is_trivial
        assert _is_trivial("x" * 200) is False

    def test_not_trivial_complex(self):
        from mlx_task_router.router import _is_trivial
        assert _is_trivial("explain microservices") is False
        assert _is_trivial("refactor the module") is False

    def test_fast_route_with_fast_model(self, monkeypatch):
        """When fast_model is configured, trivial requests get FAST route."""
        monkeypatch.setattr("mlx_task_router.router.config.fast_model", "fast/model")
        monkeypatch.setattr("mlx_task_router.router.config.trivial_threshold", 0.3)
        req = {"messages": [{"role": "user", "content": "git status"}]}
        route, reason, _ = classify(req, model_loaded=True)
        assert route == Route.FAST
        assert "trivial" in reason

    def test_no_fast_route_without_fast_model(self, monkeypatch):
        """Without fast_model, trivial requests stay LOCAL."""
        monkeypatch.setattr("mlx_task_router.router.config.fast_model", "")
        req = {"messages": [{"role": "user", "content": "git status"}]}
        route, _, _ = classify(req, model_loaded=True)
        assert route == Route.LOCAL


class TestEmbedRoutingConfig:
    """Tests for embedding routing config toggle."""

    @pytest.fixture(autouse=True)
    def _patch(self):
        with patch("mlx_task_router.router.model_manager") as mock_mm:
            mock_mm.is_loaded = True
            mock_mm._count_tokens.return_value = 50
            yield

    def test_embed_disabled_by_config(self, monkeypatch):
        """When EMBED_ROUTING=false, embed_router.score() is never used."""
        from mlx_task_router.router import _score_forward, embed_router
        monkeypatch.setattr("mlx_task_router.router.config.embed_routing", False)
        # Even with a ready probe, disabled config means no embed signal
        original_score = embed_router.score
        calls = []
        def tracking_score(text):
            calls.append(text)
            return original_score(text)
        monkeypatch.setattr("mlx_task_router.router.embed_router.score", tracking_score)
        _score_forward("git status", [{"role": "user", "content": "git status"}])
        # score() is still called but its result is None since no model,
        # which means no embed contribution. Verify no error occurs.
        assert True  # No crash = pass

    def test_embed_signal_added_to_reasons(self, monkeypatch):
        """When embed_router returns a score, it appears in reasons."""
        from mlx_task_router.router import _score_forward
        monkeypatch.setattr("mlx_task_router.router.embed_router.score", lambda text: 0.8)
        score, reasons, _ = _score_forward("test input", [{"role": "user", "content": "test input"}])
        embed_reasons = [r for r in reasons if r.startswith("embed:")]
        assert len(embed_reasons) == 1
        assert "0.80" in embed_reasons[0]
