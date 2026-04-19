"""Passthrough proxy to the upstream Anthropic API."""

from __future__ import annotations

from typing import Any, AsyncGenerator

import httpx

from mlx_task_router.config import config

_FORWARD_HEADERS = {
    "content-type",
    "anthropic-version",
    "anthropic-beta",
    "x-api-key",
    "authorization",
}

_SKIP_HEADERS = {
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "accept-encoding",
}

_TIMEOUT = httpx.Timeout(300.0, connect=30.0)


def _build_headers(incoming_headers: dict[str, str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    has_auth = False
    for key, value in incoming_headers.items():
        lower = key.lower()
        if lower in _FORWARD_HEADERS or lower.startswith("anthropic-"):
            headers[lower] = value
            if lower in ("x-api-key", "authorization"):
                has_auth = True

    if not has_auth and config.anthropic_api_key:
        headers["x-api-key"] = config.anthropic_api_key
        print("[proxy] No auth from client, using router API key")
    elif has_auth:
        print(f"[proxy] Passing through client auth ({'authorization' if 'authorization' in headers else 'x-api-key'})")

    headers.setdefault("anthropic-version", "2023-06-01")
    headers.setdefault("content-type", "application/json")
    return headers


async def forward_request(
    path: str,
    body: dict[str, Any],
    incoming_headers: dict[str, str],
) -> httpx.Response:
    headers = _build_headers(incoming_headers)
    url = f"{config.anthropic_api_url}{path}"

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(url, json=body, headers=headers)
    return response


async def stream_forward(
    path: str,
    body: dict[str, Any],
    incoming_headers: dict[str, str],
) -> AsyncGenerator[bytes, None]:
    headers = _build_headers(incoming_headers)
    url = f"{config.anthropic_api_url}{path}"

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        async with client.stream("POST", url, json=body, headers=headers) as response:
            async for chunk in response.aiter_bytes():
                yield chunk
