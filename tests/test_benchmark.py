"""Routing accuracy benchmark against labeled fixture data.

Run standalone:  python -m pytest tests/test_benchmark.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from mlx_task_router.router import Route, classify

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "benchmark_requests.json"


def _load_fixtures() -> list[dict[str, str]]:
    return json.loads(FIXTURE_PATH.read_text())


def _make_request(text: str) -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": text}],
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 4096,
    }


def _expected_route(item: dict[str, str]) -> str:
    return Route.LOCAL if item["expected_route"] == "local" else Route.FORWARD


def _routes_match(actual: str, expected: str) -> bool:
    """Check if actual route matches expected. FAST counts as LOCAL."""
    if actual == expected:
        return True
    if expected == Route.LOCAL and actual == Route.FAST:
        return True
    return False


class TestRoutingBenchmark:
    """Test routing accuracy against labeled benchmark fixtures."""

    @pytest.fixture(autouse=True)
    def _patch_model_manager(self):
        """Patch model_manager.is_loaded to True for routing tests."""
        with patch("mlx_task_router.router.model_manager") as mock_mm:
            mock_mm.is_loaded = True
            mock_mm._count_tokens.return_value = 100
            yield

    @pytest.fixture
    def fixtures(self) -> list[dict[str, str]]:
        return _load_fixtures()

    def test_fixture_file_exists(self):
        assert FIXTURE_PATH.exists(), f"Benchmark fixture not found: {FIXTURE_PATH}"

    def test_fixture_has_entries(self, fixtures):
        assert len(fixtures) >= 53, f"Expected at least 53 fixtures, got {len(fixtures)}"

    def test_all_entries_have_required_fields(self, fixtures):
        for i, item in enumerate(fixtures):
            assert "text" in item, f"Fixture {i} missing 'text'"
            assert "expected_route" in item, f"Fixture {i} missing 'expected_route'"
            assert item["expected_route"] in ("local", "forward"), (
                f"Fixture {i} has invalid expected_route: {item['expected_route']}"
            )

    def test_routing_accuracy(self, fixtures):
        """Core benchmark: check routing accuracy against labeled data."""
        correct = 0
        failures = []

        for item in fixtures:
            request = _make_request(item["text"])
            route, reason, _ = classify(request, model_loaded=True)
            expected = _expected_route(item)

            if _routes_match(route, expected):
                correct += 1
            else:
                failures.append({
                    "text": item["text"],
                    "expected": expected,
                    "actual": route,
                    "reason": reason,
                    "category": item.get("category", "unknown"),
                })

        total = len(fixtures)
        accuracy = correct / total * 100

        # Print report
        print(f"\n{'='*60}")
        print(f"Routing Benchmark: {correct}/{total} correct ({accuracy:.1f}%)")
        print(f"{'='*60}")

        if failures:
            print(f"\nMisclassified ({len(failures)}):")
            for f in failures:
                print(f"  [{f['category']}] '{f['text'][:60]}' — expected {f['expected']}, got {f['actual']} ({f['reason'][:80]})")

        # Require at least 85% accuracy
        assert accuracy >= 85.0, (
            f"Routing accuracy {accuracy:.1f}% is below 85% threshold. "
            f"Failures: {len(failures)}/{total}"
        )

    def test_cli_category_accuracy(self, fixtures):
        """CLI tasks should have high local routing accuracy."""
        cli_items = [f for f in fixtures if f.get("category") == "cli"]
        correct = sum(
            1 for item in cli_items
            if classify(_make_request(item["text"]), model_loaded=True)[0] in (Route.LOCAL, Route.FAST)
        )
        accuracy = correct / len(cli_items) * 100 if cli_items else 0
        assert accuracy >= 80.0, f"CLI accuracy {accuracy:.1f}% below 80%"

    def test_neutral_category_routes_local(self, fixtures):
        """Neutral/ambiguous messages should default to LOCAL with aggressive routing."""
        neutral_items = [f for f in fixtures if f.get("category") == "neutral"]
        correct = sum(
            1 for item in neutral_items
            if classify(_make_request(item["text"]), model_loaded=True)[0] in (Route.LOCAL, Route.FAST)
        )
        accuracy = correct / len(neutral_items) * 100 if neutral_items else 0
        assert accuracy >= 90.0, f"Neutral accuracy {accuracy:.1f}% below 90%"

    def test_complex_category_stays_local(self, fixtures):
        """Single-signal complex tasks stay local — Qwen3.6-27B handles these."""
        complex_items = [f for f in fixtures if f.get("category") == "complex"]
        correct = sum(
            1 for item in complex_items
            if classify(_make_request(item["text"]), model_loaded=True)[0] in (Route.LOCAL, Route.FAST)
        )
        accuracy = correct / len(complex_items) * 100 if complex_items else 0
        assert accuracy >= 80.0, f"Complex-local accuracy {accuracy:.1f}% below 80%"

    def test_complex_multi_category_forwards(self, fixtures):
        """Multi-signal complex tasks (stacked signals) forward to Opus."""
        multi_items = [f for f in fixtures if f.get("category") == "complex_multi"]
        correct = sum(
            1 for item in multi_items
            if classify(_make_request(item["text"]), model_loaded=True)[0] == Route.FORWARD
        )
        accuracy = correct / len(multi_items) * 100 if multi_items else 0
        assert accuracy >= 80.0, f"Complex-multi accuracy {accuracy:.1f}% below 80%"

    def test_overrides_always_correct(self, fixtures):
        """@cloud/@local overrides must be 100% correct."""
        override_items = [f for f in fixtures if f.get("category") == "override"]
        for item in override_items:
            route, _, _ = classify(_make_request(item["text"]), model_loaded=True)
            expected = _expected_route(item)
            assert route == expected, (
                f"Override failed: '{item['text']}' expected {expected}, got {route}"
            )
