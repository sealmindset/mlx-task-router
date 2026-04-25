"""Request performance metrics with ring buffer storage."""

from __future__ import annotations

import statistics
import threading
import time
from dataclasses import dataclass, field
from typing import Any

_MAX_METRICS = 500


@dataclass
class RequestMetric:
    timestamp: float
    route: str  # "local" | "forward" | "cache"
    total_ms: float
    routing_ms: float = 0.0
    generation_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def tokens_per_sec(self) -> float:
        if self.route != "local" or self.generation_ms <= 0 or self.output_tokens <= 0:
            return 0.0
        return self.output_tokens / (self.generation_ms / 1000.0)


class PerfMetrics:
    def __init__(self, max_entries: int = _MAX_METRICS):
        self._lock = threading.Lock()
        self._buffer: list[RequestMetric] = []
        self._max = max_entries

    def record(self, metric: RequestMetric) -> None:
        with self._lock:
            self._buffer.append(metric)
            if len(self._buffer) > self._max:
                self._buffer = self._buffer[-self._max :]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            metrics = list(self._buffer)

        if not metrics:
            return {
                "total_recorded": 0,
                "latency_p50_ms": 0,
                "latency_p95_ms": 0,
                "latency_p99_ms": 0,
                "local_tokens_per_sec": 0,
                "local_avg_generation_ms": 0,
                "forward_avg_latency_ms": 0,
                "routing_avg_ms": 0,
                "requests_last_hour": 0,
                "local_count": 0,
                "forward_count": 0,
                "cache_count": 0,
            }

        now = time.time()
        last_hour = [m for m in metrics if now - m.timestamp < 3600]
        all_latencies = [m.total_ms for m in metrics]

        local_metrics = [m for m in metrics if m.route == "local"]
        forward_metrics = [m for m in metrics if m.route == "forward"]
        cache_metrics = [m for m in metrics if m.route == "cache"]

        local_tps = [m.tokens_per_sec for m in local_metrics if m.tokens_per_sec > 0]
        local_gen = [m.generation_ms for m in local_metrics if m.generation_ms > 0]
        forward_lat = [m.total_ms for m in forward_metrics]
        routing_times = [m.routing_ms for m in metrics if m.routing_ms > 0]

        return {
            "total_recorded": len(metrics),
            "latency_p50_ms": round(_percentile(all_latencies, 50), 1),
            "latency_p95_ms": round(_percentile(all_latencies, 95), 1),
            "latency_p99_ms": round(_percentile(all_latencies, 99), 1),
            "local_tokens_per_sec": round(statistics.mean(local_tps), 1) if local_tps else 0,
            "local_avg_generation_ms": round(statistics.mean(local_gen), 1) if local_gen else 0,
            "forward_avg_latency_ms": round(statistics.mean(forward_lat), 1) if forward_lat else 0,
            "routing_avg_ms": round(statistics.mean(routing_times), 2) if routing_times else 0,
            "requests_last_hour": len(last_hour),
            "local_count": len(local_metrics),
            "forward_count": len(forward_metrics),
            "cache_count": len(cache_metrics),
        }


def _percentile(data: list[float], pct: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = (pct / 100.0) * (len(sorted_data) - 1)
    lower = int(idx)
    upper = min(lower + 1, len(sorted_data) - 1)
    frac = idx - lower
    return sorted_data[lower] * (1 - frac) + sorted_data[upper] * frac


perf_metrics = PerfMetrics()
