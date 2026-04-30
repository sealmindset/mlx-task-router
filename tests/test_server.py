"""Server-level tests using FastAPI TestClient.

Tests the full HTTP contract: routing decisions, streaming vs non-streaming,
fallback-on-error, cache interaction, stat recording, CORS, and auth passthrough.
Model manager is mocked — these test the server logic, not MLX inference.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_body(content: str = "git status", stream: bool = False, **kwargs):
    body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": content}],
        "stream": stream,
        **kwargs,
    }
    return body


def _local_generate_result():
    return {
        "id": "msg_local_test",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "On branch main"}],
        "model": "claude-sonnet-4-20250514",
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


def _sse_events():
    return [
        'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_local_test","type":"message","role":"assistant","content":[],"model":"test","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":10,"output_tokens":0,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}\n\n',
        'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
        'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"On branch main"}}\n\n',
        'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
        'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":5}}\n\n',
        'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """Create a TestClient with model_manager mocked as loaded."""
    with patch("mlx_task_router.server.model_manager") as mock_mm, \
         patch("mlx_task_router.server.init_watchdog") as mock_wd_init, \
         patch("mlx_task_router.server.shutdown_client", new_callable=AsyncMock), \
         patch("mlx_task_router.server.stats") as mock_stats, \
         patch("mlx_task_router.watchdog.watchdog", None):

        mock_mm.is_loaded = True
        mock_mm.is_loading = False
        mock_mm.current_model = "test-model"
        mock_mm.generate.return_value = _local_generate_result()
        mock_mm.stream_generate.return_value = iter(_sse_events())
        mock_mm.unload.return_value = None
        mock_mm.load_model.return_value = None

        mock_wd = MagicMock()
        mock_wd.is_healthy = True
        mock_wd.start.return_value = None
        mock_wd.stop.return_value = None
        mock_wd_init.return_value = mock_wd

        mock_stats.start.return_value = None
        mock_stats.stop.return_value = None
        mock_stats.record_local.return_value = None
        mock_stats.record_forward.return_value = None
        mock_stats.get.return_value = {
            "requests_total": 0, "requests_local": 0,
            "requests_forwarded": 0, "cost_saved_usd": 0.0,
            "cost_saved_display": "$0.0000",
        }

        # Patch watchdog health check in router module
        with patch("mlx_task_router.server._wd_ref", mock_wd):
            from mlx_task_router.server import app
            with TestClient(app) as tc:
                yield tc


@pytest.fixture
def client_no_model():
    """Create a TestClient with model not loaded (fail-open scenario)."""
    with patch("mlx_task_router.server.model_manager") as mock_mm, \
         patch("mlx_task_router.server.init_watchdog") as mock_wd_init, \
         patch("mlx_task_router.server.shutdown_client", new_callable=AsyncMock), \
         patch("mlx_task_router.server.stats") as mock_stats, \
         patch("mlx_task_router.watchdog.watchdog", None):

        mock_mm.is_loaded = False
        mock_mm.is_loading = False
        mock_mm.current_model = None
        mock_mm.unload.return_value = None
        mock_mm.load_model.return_value = None

        mock_wd = MagicMock()
        mock_wd.is_healthy = False
        mock_wd.start.return_value = None
        mock_wd.stop.return_value = None
        mock_wd_init.return_value = mock_wd

        mock_stats.start.return_value = None
        mock_stats.stop.return_value = None
        mock_stats.record_local.return_value = None
        mock_stats.record_forward.return_value = None
        mock_stats.get.return_value = {
            "requests_total": 0, "requests_local": 0,
            "requests_forwarded": 0, "cost_saved_usd": 0.0,
            "cost_saved_display": "$0.0000",
        }

        with patch("mlx_task_router.server._wd_ref", mock_wd):
            from mlx_task_router.server import app
            with TestClient(app) as tc:
                yield tc


# ---------------------------------------------------------------------------
# Tests — Root & Info Endpoints
# ---------------------------------------------------------------------------

class TestRootEndpoint:
    def test_root_returns_service_info(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "mlx-task-router"
        assert "version" in data

    def test_root_shows_model_status(self, client):
        resp = client.get("/")
        data = resp.json()
        assert "model_loaded" in data


# ---------------------------------------------------------------------------
# Tests — Non-Streaming Messages
# ---------------------------------------------------------------------------

class TestNonStreamingMessages:
    def test_simple_request_routes_local(self, client):
        resp = client.post(
            "/v1/messages",
            json=_make_body("git status"),
            headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "message"
        assert data["role"] == "assistant"
        assert "content" in data
        assert "usage" in data

    def test_response_has_anthropic_format(self, client):
        resp = client.post(
            "/v1/messages",
            json=_make_body("ls -la"),
            headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
        )
        data = resp.json()
        assert "id" in data
        assert "stop_reason" in data
        assert "usage" in data
        usage = data["usage"]
        assert "input_tokens" in usage
        assert "output_tokens" in usage


# ---------------------------------------------------------------------------
# Tests — Streaming Messages
# ---------------------------------------------------------------------------

class TestStreamingMessages:
    def test_streaming_returns_sse(self, client):
        client.post("/cache/clear")
        resp = client.post(
            "/v1/messages",
            json=_make_body("git log --oneline", stream=True),
            headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_streaming_contains_required_events(self, client):
        client.post("/cache/clear")
        resp = client.post(
            "/v1/messages",
            json=_make_body("git diff HEAD", stream=True),
            headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
        )
        body = resp.text
        assert "message_start" in body
        assert "message_stop" in body


# ---------------------------------------------------------------------------
# Tests — @cloud / @local Overrides
# ---------------------------------------------------------------------------

class TestRoutingOverrides:
    def test_at_local_routes_locally(self, client):
        """@local should force local routing even for complex requests."""
        resp = client.post(
            "/v1/messages",
            json=_make_body("@local Explain the routing algorithm"),
            headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "message"


# ---------------------------------------------------------------------------
# Tests — Fail-Open (Model Not Loaded)
# ---------------------------------------------------------------------------

class TestFailOpen:
    def test_no_model_forwards_request(self, client_no_model):
        """When model is not loaded, requests should forward to API."""
        with patch("mlx_task_router.server.forward_request", new_callable=AsyncMock) as mock_fwd:
            mock_fwd.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={
                    "id": "msg_fwd",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "forwarded"}],
                    "model": "claude-sonnet-4-20250514",
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }),
            )
            resp = client_no_model.post(
                "/v1/messages",
                json=_make_body("git status"),
                headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
            )
            assert resp.status_code == 200
            assert mock_fwd.called


# ---------------------------------------------------------------------------
# Tests — Cache Interaction
# ---------------------------------------------------------------------------

class TestCacheInteraction:
    def test_second_request_hits_cache(self, client):
        body = _make_body("echo hello")
        headers = {"x-api-key": "test-key", "anthropic-version": "2023-06-01"}

        resp1 = client.post("/v1/messages", json=body, headers=headers)
        assert resp1.status_code == 200

        resp2 = client.post("/v1/messages", json=body, headers=headers)
        assert resp2.status_code == 200
        assert resp1.json()["content"] == resp2.json()["content"]

    def test_cache_clear_endpoint(self, client):
        resp = client.post("/cache/clear")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cleared"


# ---------------------------------------------------------------------------
# Tests — Stats Endpoints
# ---------------------------------------------------------------------------

class TestStatsEndpoints:
    def test_get_stats(self, client):
        resp = client.get("/stats")
        assert resp.status_code == 200

    def test_reset_stats(self, client):
        resp = client.post("/stats/reset")
        assert resp.status_code == 200

    def test_perf_endpoint(self, client):
        resp = client.get("/perf")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_recorded" in data

    def test_cache_stats(self, client):
        resp = client.get("/cache")
        assert resp.status_code == 200
        data = resp.json()
        assert "hits" in data
        assert "misses" in data


# ---------------------------------------------------------------------------
# Tests — CORS
# ---------------------------------------------------------------------------

class TestCORS:
    def test_cors_headers_present(self, client):
        resp = client.options(
            "/v1/messages",
            headers={
                "origin": "http://localhost:3000",
                "access-control-request-method": "POST",
            },
        )
        assert "access-control-allow-origin" in resp.headers

    def test_cors_allows_any_origin(self, client):
        resp = client.get(
            "/",
            headers={"origin": "https://example.com"},
        )
        assert resp.headers.get("access-control-allow-origin") == "*"


# ---------------------------------------------------------------------------
# Tests — Token Count Endpoint
# ---------------------------------------------------------------------------

class TestTokenCount:
    def test_count_tokens_endpoint_exists(self, client):
        resp = client.post(
            "/v1/messages/count_tokens",
            json={
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "hello"}],
            },
            headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
        )
        # May forward to API or count locally — either way should not 404
        assert resp.status_code != 404


# ---------------------------------------------------------------------------
# Tests — Routing History Endpoints
# ---------------------------------------------------------------------------

class TestRoutingHistoryEndpoints:
    def test_routing_history_endpoint(self, client):
        # Make a request to generate a routing decision
        client.post(
            "/v1/messages",
            json=_make_body("pwd"),
            headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
        )
        resp = client.get("/routing/history")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        assert "route" in data[0]
        assert "message_preview" in data[0]

    def test_routing_summary(self, client):
        resp = client.get("/routing/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "local" in data

    def test_routing_clear(self, client):
        resp = client.post("/routing/clear")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cleared"


# ---------------------------------------------------------------------------
# Tests — Semantic Cache Endpoints
# ---------------------------------------------------------------------------

class TestSemanticCacheEndpoints:
    def test_semantic_cache_stats(self, client):
        resp = client.get("/semantic-cache")
        assert resp.status_code == 200
        data = resp.json()
        assert "hits" in data
        assert "threshold" in data

    def test_semantic_cache_clear(self, client):
        resp = client.post("/semantic-cache/clear")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cleared"


# ---------------------------------------------------------------------------
# Tests — Config Endpoints
# ---------------------------------------------------------------------------

class TestConfigEndpoints:
    def test_get_config(self, client):
        resp = client.get("/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "model_name" in data
        assert "temperature" in data
        assert "routing_threshold" in data

    def test_config_reload(self, client):
        resp = client.post("/config/reload")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "reloaded"
        assert "changes" in data


# ---------------------------------------------------------------------------
# Tests — Annealing Endpoints
# ---------------------------------------------------------------------------

class TestAnnealingEndpoints:
    def test_annealing_status(self, client):
        resp = client.get("/annealing")
        assert resp.status_code == 200
        data = resp.json()
        assert "adjustments" in data
        assert "learning_rate" in data

    def test_annealing_reset(self, client):
        resp = client.post("/annealing/reset")
        assert resp.status_code == 200
        assert resp.json()["status"] == "reset"


# ---------------------------------------------------------------------------
# Tests — Embed Router Endpoints
# ---------------------------------------------------------------------------

class TestEmbedEndpoints:
    def test_embed_status(self, client):
        resp = client.get("/embed")
        assert resp.status_code == 200
        data = resp.json()
        assert "ready" in data
        assert "enabled" in data
        assert "training_samples" in data


# ---------------------------------------------------------------------------
# Tests — Model Pool Endpoints
# ---------------------------------------------------------------------------

class TestPoolEndpoints:
    def test_pool_status(self, client):
        resp = client.get("/pool")
        assert resp.status_code == 200
        data = resp.json()
        assert "main_loaded" in data or "status" in data


# ---------------------------------------------------------------------------
# Tests — Session Stats Endpoints
# ---------------------------------------------------------------------------

class TestSessionEndpoints:
    def test_sessions_list(self, client):
        resp = client.get("/sessions")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_sessions_summary(self, client):
        resp = client.get("/sessions/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_sessions" in data
        assert "active_sessions" in data

    def test_sessions_current_empty(self, client):
        client.post("/sessions/clear")
        resp = client.get("/sessions/current")
        assert resp.status_code == 200
        assert resp.json()["session"] is None

    def test_sessions_after_request(self, client):
        client.post("/sessions/clear")
        client.post(
            "/v1/messages",
            json=_make_body("ls -la"),
            headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01",
                      "x-session-id": "test-session-1"},
        )
        resp = client.get("/sessions/current")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "test-session-1"
        assert data["requests_total"] >= 1

    def test_sessions_by_id(self, client):
        client.post("/sessions/clear")
        client.post(
            "/v1/messages",
            json=_make_body("echo hi"),
            headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01",
                      "x-session-id": "lookup-test"},
        )
        resp = client.get("/sessions/lookup-test")
        assert resp.status_code == 200
        assert resp.json()["session_id"] == "lookup-test"

    def test_sessions_not_found(self, client):
        resp = client.get("/sessions/nonexistent-session-id")
        assert resp.status_code == 404

    def test_sessions_clear(self, client):
        client.post(
            "/v1/messages",
            json=_make_body("pwd"),
            headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
        )
        resp = client.post("/sessions/clear")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cleared"
        resp = client.get("/sessions/summary")
        assert resp.json()["total_sessions"] == 0


# ---------------------------------------------------------------------------
# Tests — Trust-But-Verify Endpoints
# ---------------------------------------------------------------------------

class TestVerifyEndpoints:
    def test_verify_status(self, client):
        resp = client.get("/verify")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data
        assert "pass_rate" in data
        assert "total_verified" in data

    def test_verify_results_empty(self, client):
        resp = client.get("/verify/results")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_verify_adjustments(self, client):
        resp = client.get("/verify/adjustments")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data
        assert "adjustments" in data
        assert "total_processed" in data

    def test_verify_enable_disable(self, client):
        resp = client.post("/verify/enable", json={"enabled": False, "shadow": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert data["enabled"] is False

    def test_verify_reset(self, client):
        resp = client.post("/verify/reset")
        assert resp.status_code == 200
        assert resp.json()["status"] == "reset"


# ---------------------------------------------------------------------------
# Tests — Dashboard
# ---------------------------------------------------------------------------

class TestDashboard:
    def test_dashboard_returns_html(self, client):
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_dashboard_contains_title(self, client):
        resp = client.get("/dashboard")
        assert "MLX Task Router" in resp.text

    def test_dashboard_contains_chart(self, client):
        resp = client.get("/dashboard")
        assert "routing-chart" in resp.text
        assert "chart.js" in resp.text.lower() or "Chart" in resp.text


# ---------------------------------------------------------------------------
# Tests — Quality Assurance Endpoints
# ---------------------------------------------------------------------------

class TestQAEndpoints:
    def test_qa_status(self, client):
        resp = client.get("/qa")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data
        assert "quality" in data
        assert "cost" in data

    def test_qa_categories_empty(self, client):
        resp = client.get("/qa/categories")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_qa_evidence(self, client):
        resp = client.get("/qa/evidence")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_qa_failures(self, client):
        resp = client.get("/qa/failures")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_qa_enable_disable(self, client):
        resp = client.post("/qa/enable", json={"enabled": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert data["enabled"] is False

    def test_qa_reset(self, client):
        resp = client.post("/qa/reset")
        assert resp.status_code == 200
        assert resp.json()["status"] == "reset"

    def test_qa_cost(self, client):
        resp = client.get("/qa/cost")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_gated" in data
        assert "estimated_shadow_cost_usd" in data

    def test_qa_dashboard(self, client):
        resp = client.get("/qa/dashboard")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Quality Assurance" in resp.text
