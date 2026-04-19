"""FastAPI application — the main proxy server."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from mlx_task_router.config import config
from mlx_task_router.local import model_manager
from mlx_task_router.models import MessagesRequest, TokenCountRequest
from mlx_task_router.proxy import forward_request, stream_forward
from mlx_task_router.router import Route, classify, strip_routing_prefix


@asynccontextmanager
async def lifespan(app: FastAPI):
    gear = config.gears.get(config.default_gear)
    if gear:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, model_manager.load_gear, gear)
    else:
        print(f"[warn] Unknown default gear '{config.default_gear}', starting without model")
    yield
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

    route, reason = classify(parsed, model_manager.is_loaded)

    if config.log_routing:
        gear_name = model_manager.current_gear.name if model_manager.current_gear else "none"
        print(f"[route] {route.value} — {reason} (gear={gear_name})")

    if route == Route.LOCAL:
        return await _handle_local(parsed, body)
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
            return {"input_tokens": token_count}
        except Exception:
            pass

    incoming_headers = dict(request.headers)
    response = await forward_request("/v1/messages/count_tokens", body, incoming_headers)
    return JSONResponse(content=response.json(), status_code=response.status_code)


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
    gear = model_manager.current_gear
    return {
        "status": "healthy",
        "model_loaded": model_manager.is_loaded,
        "gear": gear.name if gear else None,
        "loading": model_manager.is_loading,
    }


@app.get("/")
async def root():
    gear = model_manager.current_gear
    return {
        "service": "mlx-task-router",
        "version": "0.1.0",
        "model_loaded": model_manager.is_loaded,
        "gear": gear.name if gear else None,
    }


# --- Internal helpers ---


async def _handle_local(parsed: MessagesRequest, body: dict[str, Any]):
    if parsed.stream:
        return StreamingResponse(
            _stream_local(parsed),
            media_type="text/event-stream",
        )
    else:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, model_manager.generate, parsed)
        return JSONResponse(content=result)


async def _stream_local(parsed: MessagesRequest):
    loop = asyncio.get_event_loop()
    events = await loop.run_in_executor(
        None, lambda: list(model_manager.stream_generate(parsed))
    )
    for event in events:
        yield event


async def _handle_forward(
    parsed: MessagesRequest,
    body: dict[str, Any],
    request: Request,
):
    incoming_headers = dict(request.headers)

    if parsed.stream:
        return StreamingResponse(
            stream_forward("/v1/messages", body, incoming_headers),
            media_type="text/event-stream",
        )
    else:
        response = await forward_request("/v1/messages", body, incoming_headers)
        return JSONResponse(
            content=response.json(), status_code=response.status_code
        )
