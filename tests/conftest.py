from __future__ import annotations

import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    """Prevent tests from reading real .env or writing to real config dir."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "9999")
    cfg_dir = tmp_path / "config"
    monkeypatch.setattr("mlx_task_router.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("mlx_task_router.feedback._FEEDBACK_FILE", cfg_dir / "feedback.json")
    monkeypatch.setattr("mlx_task_router.stats.STATS_FILE", cfg_dir / "stats.json")


@pytest.fixture
def sample_messages():
    return [
        {"role": "user", "content": "git status"},
    ]


@pytest.fixture
def sample_tools():
    return [
        {
            "name": "Bash",
            "description": "Run shell commands",
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        }
    ]
