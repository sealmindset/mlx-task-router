"""Health check watchdog for the local MLX model."""

from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mlx_task_router.local import ModelManager

_CHECK_INTERVAL = int(os.getenv("WATCHDOG_INTERVAL", "30"))
_MAX_FAILURES = int(os.getenv("WATCHDOG_MAX_FAILURES", "3"))


class Watchdog:
    def __init__(self, model_manager: ModelManager):
        self._manager = model_manager
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._healthy = True
        self._consecutive_failures = 0
        self._last_check: float = 0
        self._last_error: str = ""
        self._recovering = False

    @property
    def is_healthy(self) -> bool:
        return self._healthy

    @property
    def is_recovering(self) -> bool:
        return self._recovering

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"[watchdog] Started (interval={_CHECK_INTERVAL}s, max_failures={_MAX_FAILURES})")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        print("[watchdog] Stopped")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(_CHECK_INTERVAL)
            if self._stop_event.is_set():
                break
            self._check()

    def _check(self) -> None:
        self._last_check = time.time()

        if not self._manager.is_loaded:
            return

        if self._manager.is_loading:
            return

        try:
            text = self._manager._count_tokens("watchdog ping")
            if text >= 0:
                if not self._healthy:
                    print("[watchdog] Model recovered — marking healthy")
                self._healthy = True
                self._consecutive_failures = 0
                self._last_error = ""
                return
        except Exception as e:
            self._last_error = str(e)

        self._consecutive_failures += 1
        print(f"[watchdog] Health check failed ({self._consecutive_failures}/{_MAX_FAILURES}): {self._last_error}")

        if self._consecutive_failures >= _MAX_FAILURES and self._healthy:
            self._healthy = False
            print("[watchdog] Model marked UNHEALTHY — all requests will forward to cloud")
            self._attempt_recovery()

    def _attempt_recovery(self) -> None:
        model_name = self._manager.current_model
        if not model_name:
            return

        self._recovering = True
        print(f"[watchdog] Attempting recovery — reloading '{model_name}'")
        try:
            self._manager.load_model(model_name)
            self._healthy = True
            self._consecutive_failures = 0
            self._last_error = ""
            print(f"[watchdog] Recovery successful — '{model_name}' reloaded")
        except Exception as e:
            self._last_error = str(e)
            print(f"[watchdog] Recovery failed: {e}")
        finally:
            self._recovering = False

    def status(self) -> dict:
        return {
            "healthy": self._healthy,
            "recovering": self._recovering,
            "consecutive_failures": self._consecutive_failures,
            "last_check": self._last_check,
            "last_error": self._last_error,
            "interval": _CHECK_INTERVAL,
            "max_failures": _MAX_FAILURES,
        }


watchdog: Watchdog | None = None


def init_watchdog(model_manager: ModelManager) -> Watchdog:
    global watchdog
    watchdog = Watchdog(model_manager)
    return watchdog
