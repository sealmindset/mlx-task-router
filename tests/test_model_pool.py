"""Tests for the multi-model pool module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mlx_task_router.model_pool import ModelPool


class TestModelPoolInit:
    """Tests for model pool initialization."""

    def test_pool_wraps_main_manager(self):
        mock_manager = MagicMock()
        mock_manager.is_loaded = True
        mock_manager.current_model = "test-model"
        pool = ModelPool(mock_manager)
        assert pool.is_loaded is True
        assert pool.fast_available is False

    def test_fast_not_available_initially(self):
        mock_manager = MagicMock()
        pool = ModelPool(mock_manager)
        assert pool.fast_available is False
        assert pool.fast_model_name is None

    def test_status_without_fast(self):
        mock_manager = MagicMock()
        mock_manager.is_loaded = True
        mock_manager.current_model = "main-model"
        pool = ModelPool(mock_manager)
        status = pool.status()
        assert status["main_loaded"] is True
        assert status["main_model"] == "main-model"
        assert status["fast_available"] is False
        assert status["fast_model"] is None


class TestModelPoolTierRouting:
    """Tests for tier-based model selection."""

    def test_generate_local_uses_main(self):
        mock_manager = MagicMock()
        mock_manager.generate.return_value = {"content": "main result"}
        pool = ModelPool(mock_manager)
        result = pool.generate(MagicMock(), tier="local")
        mock_manager.generate.assert_called_once()
        assert result == {"content": "main result"}

    def test_generate_fast_falls_back_to_main(self):
        """When fast model not loaded, fast tier falls back to main."""
        mock_manager = MagicMock()
        mock_manager.generate.return_value = {"content": "main result"}
        pool = ModelPool(mock_manager)
        result = pool.generate(MagicMock(), tier="fast")
        mock_manager.generate.assert_called_once()

    def test_stream_generate_local_uses_main(self):
        mock_manager = MagicMock()
        mock_manager.stream_generate.return_value = iter(["event1", "event2"])
        pool = ModelPool(mock_manager)
        events = list(pool.stream_generate(MagicMock(), tier="local"))
        mock_manager.stream_generate.assert_called_once()
        assert events == ["event1", "event2"]

    def test_stream_generate_fast_falls_back_to_main(self):
        mock_manager = MagicMock()
        mock_manager.stream_generate.return_value = iter(["event1"])
        pool = ModelPool(mock_manager)
        events = list(pool.stream_generate(MagicMock(), tier="fast"))
        mock_manager.stream_generate.assert_called_once()


class TestModelPoolFastModel:
    """Tests for fast model management."""

    def test_unload_fast(self):
        mock_manager = MagicMock()
        pool = ModelPool(mock_manager)
        pool._fast_model = MagicMock()
        pool._fast_tokenizer = MagicMock()
        pool._fast_model_name = "fast-model"
        assert pool.fast_available is True
        pool.unload_fast()
        assert pool.fast_available is False
        assert pool.fast_model_name is None

    def test_status_with_fast(self):
        mock_manager = MagicMock()
        mock_manager.is_loaded = True
        mock_manager.current_model = "main-model"
        pool = ModelPool(mock_manager)
        pool._fast_model = MagicMock()
        pool._fast_tokenizer = MagicMock()
        pool._fast_model_name = "fast-model"

        status = pool.status()
        assert status["fast_available"] is True
        assert status["fast_model"] == "fast-model"
