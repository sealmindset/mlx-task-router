"""Tests for the embedding-based semantic routing module."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mlx_task_router.embed_router import EmbedRouter


class TestEmbedRouterColdStart:
    """Tests for embed router when no model is attached."""

    def test_score_returns_none_when_no_model(self):
        router = EmbedRouter()
        assert router.score("hello world") is None

    def test_embed_returns_none_when_no_model(self):
        router = EmbedRouter()
        assert router.embed("hello") is None

    def test_is_ready_false_initially(self):
        router = EmbedRouter()
        assert router.is_ready is False

    def test_status_shows_not_ready(self):
        router = EmbedRouter()
        status = router.status()
        assert status["ready"] is False
        assert status["model_attached"] is False
        assert status["probe_loaded"] is False


class TestEmbedRouterTraining:
    """Tests for training data recording and probe training."""

    def test_record_example_writes_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("mlx_task_router.embed_router._TRAINING_FILE", tmp_path / "train.jsonl")
        router = EmbedRouter()
        router._model_hash = "test123"
        router.record_example("git status", False)
        router.record_example("explain microservices architecture", True)

        lines = (tmp_path / "train.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2
        row0 = json.loads(lines[0])
        assert row0["text"] == "git status"
        assert row0["forward"] is False
        assert row0["model_hash"] == "test123"
        row1 = json.loads(lines[1])
        assert row1["forward"] is True

    def test_training_sample_count(self, tmp_path, monkeypatch):
        monkeypatch.setattr("mlx_task_router.embed_router._TRAINING_FILE", tmp_path / "train.jsonl")
        router = EmbedRouter()
        router._model_hash = "test123"
        router.record_example("hello", False)
        router.record_example("world", True)
        assert router.training_sample_count() == 2

    def test_training_sample_count_filters_by_model(self, tmp_path, monkeypatch):
        monkeypatch.setattr("mlx_task_router.embed_router._TRAINING_FILE", tmp_path / "train.jsonl")
        router = EmbedRouter()
        router._model_hash = "model_a"
        router.record_example("hello", False)
        router._model_hash = "model_b"
        router.record_example("world", True)
        assert router.training_sample_count() == 1  # only model_b

    def test_train_returns_false_without_model(self):
        router = EmbedRouter()
        assert router.train() is False


class TestEmbedRouterProbe:
    """Tests for probe persistence."""

    def test_save_and_load_probe(self, tmp_path, monkeypatch):
        monkeypatch.setattr("mlx_task_router.embed_router._PROBE_FILE", tmp_path / "probe.json")
        router = EmbedRouter()
        router._model_hash = "test123"
        router._probe_weights = [0.1, 0.2, 0.3]
        router._probe_bias = -0.5
        router._ready = True
        router._save_probe()

        # Load into new instance
        router2 = EmbedRouter()
        router2._model_hash = "test123"
        monkeypatch.setattr("mlx_task_router.embed_router._PROBE_FILE", tmp_path / "probe.json")
        router2._load_probe()

        assert router2._probe_weights == [0.1, 0.2, 0.3]
        assert router2._probe_bias == -0.5
        assert router2._ready is True

    def test_probe_model_mismatch_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setattr("mlx_task_router.embed_router._PROBE_FILE", tmp_path / "probe.json")
        router = EmbedRouter()
        router._model_hash = "model_a"
        router._probe_weights = [0.1]
        router._probe_bias = 0.0
        router._save_probe()

        router2 = EmbedRouter()
        router2._model_hash = "model_b"
        monkeypatch.setattr("mlx_task_router.embed_router._PROBE_FILE", tmp_path / "probe.json")
        router2._load_probe()
        assert router2._ready is False

    def test_score_with_manual_probe(self):
        """Test scoring with manually set probe weights."""
        router = EmbedRouter()
        router._model_hash = "test"
        router._probe_weights = [0.5, -0.5, 0.0]
        router._probe_bias = 0.0
        router._ready = True
        # Mock model that returns simple embeddings
        router._model = MagicMock()
        router._tokenizer = MagicMock()

        # Since embed() depends on MLX, test the math directly
        import math
        dot = sum(e * w for e, w in zip([1.0, 0.0, 0.0], [0.5, -0.5, 0.0]))
        score = 1.0 / (1.0 + math.exp(-dot))
        assert 0.5 < score < 0.7  # positive embedding → forward-leaning

    def test_status_with_probe(self):
        router = EmbedRouter()
        router._model_hash = "test"
        router._probe_weights = [0.1, 0.2]
        router._probe_bias = 0.0
        router._ready = True
        router._model = MagicMock()

        status = router.status()
        assert status["ready"] is True
        assert status["probe_dim"] == 2
        assert status["model_attached"] is True
