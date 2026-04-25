"""Tests for config reload functionality."""

from __future__ import annotations

import os

from mlx_task_router.config import Config


class TestConfigReload:
    def test_reload_returns_empty_changes_when_nothing_changed(self):
        c = Config()
        changes = c.reload()
        assert changes == {}

    def test_reload_detects_temperature_change(self):
        c = Config()
        old_temp = os.environ.get("MLX_TEMPERATURE")
        try:
            os.environ["MLX_TEMPERATURE"] = "0.99"
            changes = c.reload()
            assert "temperature" in changes
            assert c.temperature == 0.99
        finally:
            if old_temp is not None:
                os.environ["MLX_TEMPERATURE"] = old_temp
            else:
                os.environ.pop("MLX_TEMPERATURE", None)
            c.reload()

    def test_reload_detects_routing_threshold_change(self):
        c = Config()
        old_val = os.environ.get("ROUTING_THRESHOLD")
        try:
            os.environ["ROUTING_THRESHOLD"] = "0.75"
            changes = c.reload()
            assert "routing_threshold" in changes
            assert c.routing_threshold == 0.75
        finally:
            if old_val is not None:
                os.environ["ROUTING_THRESHOLD"] = old_val
            else:
                os.environ.pop("ROUTING_THRESHOLD", None)
            c.reload()

    def test_reload_detects_log_routing_change(self):
        c = Config()
        old_val = os.environ.get("LOG_ROUTING")
        try:
            os.environ["LOG_ROUTING"] = "false"
            changes = c.reload()
            assert "log_routing" in changes
            assert c.log_routing is False
        finally:
            if old_val is not None:
                os.environ["LOG_ROUTING"] = old_val
            else:
                os.environ.pop("LOG_ROUTING", None)
            c.reload()
