"""Tests for the performance metrics module."""

from __future__ import annotations

import time

from mlx_task_router.perf import PerfMetrics, RequestMetric


class TestRequestMetric:
    def test_tokens_per_sec_local(self):
        m = RequestMetric(
            timestamp=time.time(), route="local",
            total_ms=500, generation_ms=400,
            output_tokens=100,
        )
        assert m.tokens_per_sec == 250.0  # 100 / 0.4s

    def test_tokens_per_sec_forward_is_zero(self):
        m = RequestMetric(
            timestamp=time.time(), route="forward",
            total_ms=1200, generation_ms=1200,
            output_tokens=500,
        )
        assert m.tokens_per_sec == 0.0

    def test_tokens_per_sec_zero_generation(self):
        m = RequestMetric(
            timestamp=time.time(), route="local",
            total_ms=10, generation_ms=0,
            output_tokens=0,
        )
        assert m.tokens_per_sec == 0.0


class TestPerfMetrics:
    def test_empty_summary(self):
        pm = PerfMetrics()
        s = pm.summary()
        assert s["total_recorded"] == 0
        assert s["latency_p50_ms"] == 0

    def test_record_and_summary(self):
        pm = PerfMetrics()
        for i in range(10):
            pm.record(RequestMetric(
                timestamp=time.time(), route="local",
                total_ms=100 + i * 10, routing_ms=1.0,
                generation_ms=90 + i * 10,
                input_tokens=50, output_tokens=20,
            ))
        s = pm.summary()
        assert s["total_recorded"] == 10
        assert s["local_count"] == 10
        assert s["forward_count"] == 0
        assert s["latency_p50_ms"] > 0
        assert s["routing_avg_ms"] == 1.0

    def test_ring_buffer_eviction(self):
        pm = PerfMetrics(max_entries=5)
        for i in range(10):
            pm.record(RequestMetric(
                timestamp=time.time(), route="local",
                total_ms=float(i * 100),
            ))
        s = pm.summary()
        assert s["total_recorded"] == 5

    def test_mixed_routes(self):
        pm = PerfMetrics()
        pm.record(RequestMetric(timestamp=time.time(), route="local", total_ms=100))
        pm.record(RequestMetric(timestamp=time.time(), route="forward", total_ms=200))
        pm.record(RequestMetric(timestamp=time.time(), route="cache", total_ms=5))
        s = pm.summary()
        assert s["local_count"] == 1
        assert s["forward_count"] == 1
        assert s["cache_count"] == 1
