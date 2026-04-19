"""FastAPI application — the main proxy server."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from typing import Any

# Force unbuffered stdout so print() flushes immediately under launchd
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from mlx_task_router.cache import response_cache
from mlx_task_router.config import config
from mlx_task_router.feedback import routing_feedback
from mlx_task_router.local import model_manager
from mlx_task_router.models import MessagesRequest, TokenCountRequest
from mlx_task_router.proxy import forward_request, stream_forward
from mlx_task_router.router import Route, classify, strip_routing_prefix, _get_latest_user_text
from mlx_task_router.stats import stats
from mlx_task_router.watchdog import init_watchdog, watchdog as _wd_ref


@asynccontextmanager
async def lifespan(app: FastAPI):
    stats.start()
    wd = init_watchdog(model_manager)
    gear = config.gears.get(config.default_gear)
    if gear:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, model_manager.load_gear, gear)
    else:
        print(f"[warn] Unknown default gear '{config.default_gear}', starting without model")
    wd.start()
    yield
    wd.stop()
    stats.stop()
    model_manager.unload()
    print("[server] Shutdown complete")


app = FastAPI(title="MLX Task Router", lifespan=lifespan)


# --- Main endpoints ---


@app.post("/v1/messages")
async def messages(request: Request):
    body = await request.json()

    try:
        parsed = MessagesRequest(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    from mlx_task_router.watchdog import watchdog
    model_available = model_manager.is_loaded and (watchdog is None or watchdog.is_healthy)
    route, reason, trigger = classify(parsed, model_available)

    if config.log_routing:
        gear_name = model_manager.current_gear.name if model_manager.current_gear else "none"
        print(f"[route] {route} — {reason} (gear={gear_name})")

    # Strip @cloud/@local prefix from the message before sending to any model
    _strip_prefix_from_request(parsed, body)

    if route == Route.LOCAL:
        latest_text = _get_latest_user_text(parsed.messages)
        tool_names = [t.name for t in parsed.tools] if parsed.tools else None

        cached = response_cache.get(latest_text, tool_names)
        if cached is not None:
            if config.log_routing:
                print(f"[cache] HIT — returning cached response (ttl={response_cache.ttl}s)")
            if trigger:
                routing_feedback.record_success(trigger)
            if parsed.stream:
                return StreamingResponse(
                    _yield_events(cached),
                    media_type="text/event-stream",
                )
            return JSONResponse(content=cached)

        return await _handle_local(parsed, body, request, latest_text, tool_names, trigger)
    else:
        return await _handle_forward(parsed, body, request)


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


@app.get("/feedback")
async def feedback_stats():
    return routing_feedback.stats()


@app.post("/feedback/reset")
async def reset_feedback():
    routing_feedback.reset()
    return {"status": "reset"}


# --- Gear management ---


@app.get("/gears")
async def list_gears():
    current = model_manager.current_gear
    gears = []
    for name, gear in config.gears.items():
        gears.append(
            {
                "name": name,
                "model": gear.model,
                "description": gear.description,
                "max_tokens": gear.max_tokens,
                "active": current is not None and current.name == name,
            }
        )
    return {"gears": gears, "loading": model_manager.is_loading}


@app.get("/gear")
async def current_gear():
    gear = model_manager.current_gear
    if gear:
        return {
            "name": gear.name,
            "model": gear.model,
            "description": gear.description,
            "loading": model_manager.is_loading,
        }
    return {"name": None, "model": None, "loading": model_manager.is_loading}


@app.post("/gear/{gear_name}")
async def switch_gear(gear_name: str):
    gear = config.gears.get(gear_name)
    if not gear:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown gear '{gear_name}'. Available: {list(config.gears.keys())}",
        )

    current = model_manager.current_gear
    if current and current.name == gear_name:
        return {"status": "already_active", "gear": gear_name}

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, model_manager.load_gear, gear)

    return {"status": "switched", "gear": gear_name, "model": gear.model}


# --- Health ---


@app.get("/health")
async def health():
    from mlx_task_router.watchdog import watchdog
    gear = model_manager.current_gear
    wd_healthy = watchdog.is_healthy if watchdog else True
    return {
        "status": "healthy" if wd_healthy else "degraded",
        "model_loaded": model_manager.is_loaded,
        "model_healthy": wd_healthy,
        "gear": gear.name if gear else None,
        "loading": model_manager.is_loading,
    }


@app.get("/watchdog")
async def watchdog_status():
    from mlx_task_router.watchdog import watchdog
    if watchdog is None:
        return {"status": "not initialized"}
    return watchdog.status()


@app.get("/")
async def root():
    gear = model_manager.current_gear
    s = stats.get()
    return {
        "service": "mlx-task-router",
        "version": "0.1.0",
        "model_loaded": model_manager.is_loaded,
        "gear": gear.name if gear else None,
        "requests_total": s["requests_total"],
        "requests_local": s["requests_local"],
        "cost_saved": s["cost_saved_display"],
    }


# --- Internal helpers ---


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
):
    try:
        if parsed.stream:
            events = await _collect_local_stream(parsed)
            if cache_key_text:
                response_cache.put(cache_key_text, events, cache_key_tools)
            if trigger:
                routing_feedback.record_success(trigger)
            return StreamingResponse(
                _yield_events(events),
                media_type="text/event-stream",
            )
        else:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, model_manager.generate, parsed)
            usage = result.get("usage", {})
            stats.record_local(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                model=parsed.model,
            )
            if cache_key_text:
                response_cache.put(cache_key_text, result, cache_key_tools)
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
    return await loop.run_in_executor(
        None, lambda: list(model_manager.stream_generate(parsed))
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
):
    incoming_headers = dict(request.headers)

    if parsed.stream:
        return StreamingResponse(
            _stream_forward_with_stats(body, incoming_headers),
            media_type="text/event-stream",
        )
    else:
        response = await forward_request("/v1/messages", body, incoming_headers)
        resp_json = response.json()
        usage = resp_json.get("usage", {})
        stats.record_forward(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
        return JSONResponse(content=resp_json, status_code=response.status_code)


async def _stream_forward_with_stats(body: dict[str, Any], incoming_headers: dict[str, str]):
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
