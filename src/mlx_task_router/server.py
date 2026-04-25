"""FastAPI application — the main proxy server."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from contextlib import asynccontextmanager
from typing import Any

# Force unbuffered stdout so print() flushes immediately under launchd
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from mlx_task_router.annealing import weight_annealer
from mlx_task_router.cache import response_cache
from mlx_task_router.dashboard import router as dashboard_router
from mlx_task_router.config import config
from mlx_task_router.feedback import routing_feedback
from mlx_task_router.perf import RequestMetric, perf_metrics
from mlx_task_router.local import model_manager
from mlx_task_router.models import MessagesRequest, TokenCountRequest
from mlx_task_router.proxy import forward_request, shutdown_client, stream_forward
from mlx_task_router.router import Route, classify, strip_routing_prefix, _get_latest_user_text
from mlx_task_router.routing_history import routing_history
from mlx_task_router.semantic_cache import semantic_cache
from mlx_task_router.session_stats import session_tracker
from mlx_task_router.stats import stats
from mlx_task_router.watchdog import init_watchdog, watchdog as _wd_ref


@asynccontextmanager
async def lifespan(app: FastAPI):
    stats.start()
    routing_feedback.start()
    weight_annealer.start()
    wd = init_watchdog(model_manager)
    if config.model_name:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, model_manager.load_model, config.model_name)
    else:
        print("[warn] No MLX_MODEL configured, starting without local model")
    wd.start()
    yield
    wd.stop()
    weight_annealer.stop()
    routing_feedback.stop()
    stats.stop()
    await shutdown_client()
    model_manager.unload()
    print("[server] Shutdown complete")


app = FastAPI(title="MLX Task Router", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard_router)


# --- Main endpoints ---


@app.post("/v1/messages")
async def messages(request: Request):
    if config.log_routing:
        auth_headers = [k for k in request.headers if k.lower() in ("x-api-key", "authorization") or k.lower().startswith("anthropic-")]
        print(f"[headers] Auth keys present: {auth_headers}")
    body = await request.json()

    try:
        parsed = MessagesRequest(**body)
    except Exception as e:
        # Validation failed — forward the raw body to upstream API so the
        # proxy never blocks requests due to schema drift (e.g. new content
        # block types like redacted_thinking, document, server_tool_use).
        print(f"[parse] Validation failed, forwarding raw request: {e}")
        incoming_headers = dict(request.headers)
        is_stream = body.get("stream", False)
        if is_stream:
            return StreamingResponse(
                _stream_forward_with_stats(body, incoming_headers),
                media_type="text/event-stream",
            )
        response = await forward_request("/v1/messages", body, incoming_headers)
        resp_json = response.json()
        usage = resp_json.get("usage", {})
        stats.record_forward(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
        return JSONResponse(content=resp_json, status_code=response.status_code)

    from mlx_task_router.watchdog import watchdog
    model_available = model_manager.is_loaded and (watchdog is None or watchdog.is_healthy)
    t_route_start = time.time()
    route, reason, trigger = classify(parsed, model_available)
    routing_ms = (time.time() - t_route_start) * 1000

    model_name = model_manager.current_model or "none"
    if config.log_routing:
        print(f"[route] {route} — {reason} (model={model_name})")

    latest_text = _get_latest_user_text(parsed.messages)
    routing_history.record(
        route=route, reason=reason, trigger=trigger,
        message_text=latest_text,
        model=model_name,
    )

    # Parse forward score from reason for session tracking
    _fwd_score = 0.0
    if "fwd=" in reason:
        try:
            _fwd_score = float(reason.split("fwd=")[1].split(" ")[0])
        except (ValueError, IndexError):
            pass
    _session_headers = {k.lower(): v for k, v in request.headers.items()}
    session_tracker.record(
        route=route, trigger=trigger,
        forward_score=_fwd_score,
        message_preview=latest_text[:80],
        model=model_name,
        headers=_session_headers,
    )

    # Strip @cloud/@local prefix from the message before sending to any model
    _strip_prefix_from_request(parsed, body)

    if route == Route.LOCAL:
        tool_names = [t.name for t in parsed.tools] if parsed.tools else None

        cached = response_cache.get(latest_text, tool_names)
        cache_source = "exact"
        if cached is None:
            cached = semantic_cache.get(latest_text, tool_names)
            cache_source = "semantic"
        if cached is not None:
            if config.log_routing:
                print(f"[cache] HIT ({cache_source}) — returning cached response")
            if trigger:
                routing_feedback.record_success(trigger)
            perf_metrics.record(RequestMetric(
                timestamp=time.time(), route="cache",
                total_ms=(time.time() - t_route_start) * 1000,
                routing_ms=routing_ms,
            ))
            if parsed.stream:
                return StreamingResponse(
                    _yield_events(cached),
                    media_type="text/event-stream",
                )
            return JSONResponse(content=cached)

        return await _handle_local(
            parsed, body, request, latest_text, tool_names, trigger,
            routing_ms=routing_ms,
        )
    else:
        return await _handle_forward(parsed, body, request, routing_ms=routing_ms)


@app.post("/v1/messages/count_tokens")
async def count_tokens(request: Request):
    body = await request.json()

    if model_manager.is_loaded:
        try:
            parsed = TokenCountRequest(**body)
            messages_raw = [
                m.model_dump() if hasattr(m, "model_dump") else m
                for m in parsed.messages
            ]
            from mlx_task_router.tool_format import anthropic_messages_to_chat

            chat = anthropic_messages_to_chat(messages_raw, parsed.system)
            text = "\n".join(m.get("content", "") for m in chat if isinstance(m.get("content"), str))
            token_count = model_manager._count_tokens(text)
            if config.log_routing:
                print(f"[tokens] Counted {token_count} tokens locally (saved API call)")
            return {"input_tokens": token_count}
        except Exception as e:
            print(f"[tokens] Local count failed ({e}), forwarding to API")

    incoming_headers = dict(request.headers)
    response = await forward_request("/v1/messages/count_tokens", body, incoming_headers)
    return JSONResponse(content=response.json(), status_code=response.status_code)


# --- Stats ---


@app.get("/stats")
async def get_stats():
    return stats.get()


@app.post("/stats/reset")
async def reset_stats():
    stats.reset()
    return {"status": "reset"}


@app.get("/cache")
async def cache_stats():
    return response_cache.stats()


@app.post("/cache/clear")
async def clear_cache():
    response_cache.clear()
    return {"status": "cleared"}


@app.get("/semantic-cache")
async def semantic_cache_stats():
    return semantic_cache.stats()


@app.post("/semantic-cache/clear")
async def clear_semantic_cache():
    semantic_cache.clear()
    return {"status": "cleared"}


@app.get("/feedback")
async def feedback_stats():
    return routing_feedback.stats()


@app.post("/feedback/reset")
async def reset_feedback():
    routing_feedback.reset()
    return {"status": "reset"}


@app.get("/routing/history")
async def get_routing_history(limit: int = 50):
    return routing_history.get_history(limit=min(limit, 100))


@app.get("/routing/summary")
async def get_routing_summary():
    return routing_history.summary()


@app.post("/routing/clear")
async def clear_routing_history():
    routing_history.clear()
    return {"status": "cleared"}


@app.get("/annealing")
async def annealing_status():
    return weight_annealer.status()


@app.post("/annealing/reset")
async def reset_annealing():
    weight_annealer.reset()
    return {"status": "reset"}


# --- Sessions ---


@app.get("/sessions")
async def get_sessions(limit: int = 20):
    return session_tracker.get_all_sessions(limit=min(limit, 50))


@app.get("/sessions/current")
async def get_current_session():
    current = session_tracker.get_current_session()
    if current is None:
        return {"session": None}
    return current


@app.get("/sessions/summary")
async def get_sessions_summary():
    return session_tracker.summary()


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    session = session_tracker.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return session


@app.post("/sessions/clear")
async def clear_sessions():
    session_tracker.clear()
    return {"status": "cleared"}


# --- Health ---


@app.get("/health")
async def health():
    from mlx_task_router.watchdog import watchdog
    wd_healthy = watchdog.is_healthy if watchdog else True
    return {
        "status": "healthy" if wd_healthy else "degraded",
        "model_loaded": model_manager.is_loaded,
        "model_healthy": wd_healthy,
        "model": model_manager.current_model,
        "loading": model_manager.is_loading,
    }


@app.get("/watchdog")
async def watchdog_status():
    from mlx_task_router.watchdog import watchdog
    if watchdog is None:
        return {"status": "not initialized"}
    return watchdog.status()


@app.get("/perf")
async def perf_stats():
    return perf_metrics.summary()


# --- Config ---


@app.get("/config")
async def get_config():
    return {
        "model_name": config.model_name,
        "model_max_tokens": config.model_max_tokens,
        "temperature": config.temperature,
        "top_p": config.top_p,
        "top_k": config.top_k,
        "repetition_penalty": config.repetition_penalty,
        "routing_threshold": config.routing_threshold,
        "adaptive_routing": config.adaptive_routing,
        "log_routing": config.log_routing,
        "max_local_context_tokens": config.max_local_context_tokens,
    }


@app.post("/config/reload")
async def reload_config():
    old_model = config.model_name
    changes = config.reload()

    model_changed = "model_name" in changes
    reload_needed = model_changed and model_manager.is_loaded

    if reload_needed:
        print(f"[config] Model changed ({old_model} → {config.model_name}), reloading...")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, model_manager.load_model, config.model_name)

    print(f"[config] Reloaded. Changes: {changes if changes else 'none'}")
    return {
        "status": "reloaded",
        "changes": changes,
        "model_reloaded": reload_needed,
    }


@app.get("/")
async def root():
    s = stats.get()
    return {
        "service": "mlx-task-router",
        "version": "0.6.1",
        "model_loaded": model_manager.is_loaded,
        "model": model_manager.current_model,
        "requests_total": s["requests_total"],
        "requests_local": s["requests_local"],
        "cost_saved": s["cost_saved_display"],
    }


# --- Internal helpers ---

_GENERATION_TIMEOUT = int(__import__("os").getenv("MLX_GENERATION_TIMEOUT", "120"))


def _extract_tokens_from_events(events: list[str]) -> tuple[int, int]:
    """Extract input/output token counts from buffered SSE events."""
    in_tok, out_tok = 0, 0
    for event in events:
        try:
            if "message_start" in event:
                data = json.loads(event.split("data: ", 1)[1])
                in_tok = data.get("message", {}).get("usage", {}).get("input_tokens", 0)
            elif "message_delta" in event:
                data = json.loads(event.split("data: ", 1)[1])
                out_tok = data.get("usage", {}).get("output_tokens", 0)
        except (json.JSONDecodeError, IndexError):
            pass
    return in_tok, out_tok


def _strip_prefix_from_request(parsed: MessagesRequest, body: dict[str, Any]):
    """Remove @cloud/@local prefix from the latest user message in both parsed and raw body."""
    for msg_list in (parsed.messages, body.get("messages", [])):
        if not msg_list:
            continue
        for msg in reversed(msg_list):
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
            if role != "user":
                continue
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
            if isinstance(content, str):
                stripped = strip_routing_prefix(content)
                if stripped != content:
                    if isinstance(msg, dict):
                        msg["content"] = stripped
                    else:
                        msg.content = stripped
            elif isinstance(content, list):
                for block in content:
                    b = block if isinstance(block, dict) else None
                    if b and b.get("type") == "text":
                        b["text"] = strip_routing_prefix(b.get("text", ""))
                    elif hasattr(block, "type") and block.type == "text":
                        block.text = strip_routing_prefix(block.text)
            break


async def _handle_local(
    parsed: MessagesRequest,
    body: dict[str, Any],
    request: Request | None = None,
    cache_key_text: str | None = None,
    cache_key_tools: list[str] | None = None,
    trigger: str = "",
    routing_ms: float = 0.0,
):
    try:
        t_gen_start = time.time()
        if parsed.stream:
            events = await _collect_local_stream(parsed)
            gen_ms = (time.time() - t_gen_start) * 1000
            if cache_key_text:
                response_cache.put(cache_key_text, events, cache_key_tools)
                semantic_cache.put(cache_key_text, events, cache_key_tools)
            if trigger:
                routing_feedback.record_success(trigger)
            # Extract token counts from SSE events for perf metrics
            _in, _out = _extract_tokens_from_events(events)
            perf_metrics.record(RequestMetric(
                timestamp=time.time(), route="local",
                total_ms=routing_ms + gen_ms,
                routing_ms=routing_ms, generation_ms=gen_ms,
                input_tokens=_in, output_tokens=_out,
            ))
            return StreamingResponse(
                _yield_events(events),
                media_type="text/event-stream",
            )
        else:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, model_manager.generate, parsed),
                timeout=_GENERATION_TIMEOUT,
            )
            gen_ms = (time.time() - t_gen_start) * 1000
            usage = result.get("usage", {})
            in_tok = usage.get("input_tokens", 0)
            out_tok = usage.get("output_tokens", 0)
            stats.record_local(
                input_tokens=in_tok,
                output_tokens=out_tok,
                model=parsed.model,
            )
            perf_metrics.record(RequestMetric(
                timestamp=time.time(), route="local",
                total_ms=routing_ms + gen_ms,
                routing_ms=routing_ms, generation_ms=gen_ms,
                input_tokens=in_tok, output_tokens=out_tok,
            ))
            if cache_key_text:
                response_cache.put(cache_key_text, result, cache_key_tools)
                semantic_cache.put(cache_key_text, result, cache_key_tools)
            if trigger:
                routing_feedback.record_success(trigger)
            return JSONResponse(content=result)
    except Exception as e:
        print(f"[fallback] Local generation failed: {e}")
        if trigger:
            routing_feedback.record_failure(trigger)
            print(f"[feedback] Recorded failure for trigger '{trigger}'")
        if request:
            print("[fallback] Retrying via Anthropic API")
            return await _handle_forward(parsed, body, request)
        raise


async def _collect_local_stream(parsed: MessagesRequest) -> list[str]:
    loop = asyncio.get_event_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(
            None, lambda: list(model_manager.stream_generate(parsed))
        ),
        timeout=_GENERATION_TIMEOUT,
    )


async def _yield_events(events: list[str]):
    input_tokens = 0
    output_tokens = 0
    model = ""

    for event in events:
        yield event
        if "message_start" in event:
            try:
                data = json.loads(event.split("data: ", 1)[1])
                input_tokens = data.get("message", {}).get("usage", {}).get("input_tokens", 0)
                model = data.get("message", {}).get("model", "")
            except (json.JSONDecodeError, IndexError):
                pass
        elif "message_delta" in event:
            try:
                data = json.loads(event.split("data: ", 1)[1])
                output_tokens = data.get("usage", {}).get("output_tokens", 0)
            except (json.JSONDecodeError, IndexError):
                pass

    stats.record_local(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
    )


async def _handle_forward(
    parsed: MessagesRequest,
    body: dict[str, Any],
    request: Request,
    routing_ms: float = 0.0,
):
    incoming_headers = dict(request.headers)

    t_fwd_start = time.time()
    if parsed.stream:
        return StreamingResponse(
            _stream_forward_with_stats(body, incoming_headers, routing_ms=routing_ms),
            media_type="text/event-stream",
        )
    else:
        response = await forward_request("/v1/messages", body, incoming_headers)
        fwd_ms = (time.time() - t_fwd_start) * 1000
        resp_json = response.json()
        usage = resp_json.get("usage", {})
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        stats.record_forward(input_tokens=in_tok, output_tokens=out_tok)
        perf_metrics.record(RequestMetric(
            timestamp=time.time(), route="forward",
            total_ms=routing_ms + fwd_ms,
            routing_ms=routing_ms, generation_ms=fwd_ms,
            input_tokens=in_tok, output_tokens=out_tok,
        ))
        return JSONResponse(content=resp_json, status_code=response.status_code)


async def _stream_forward_with_stats(
    body: dict[str, Any], incoming_headers: dict[str, str], routing_ms: float = 0.0,
):
    input_tokens = 0
    output_tokens = 0

    async for chunk in stream_forward("/v1/messages", body, incoming_headers):
        yield chunk
        # Best-effort token extraction from streamed chunks
        try:
            text = chunk.decode("utf-8", errors="ignore")
            for line in text.split("\n"):
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])
                if data.get("type") == "message_start":
                    input_tokens = data.get("message", {}).get("usage", {}).get("input_tokens", 0)
                elif data.get("type") == "message_delta":
                    output_tokens = data.get("usage", {}).get("output_tokens", 0)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    stats.record_forward(input_tokens=input_tokens, output_tokens=output_tokens)
    perf_metrics.record(RequestMetric(
        timestamp=time.time(), route="forward",
        total_ms=0,  # streaming — total_ms not easily captured
        routing_ms=routing_ms,
        input_tokens=input_tokens, output_tokens=output_tokens,
    ))
