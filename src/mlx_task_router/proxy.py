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
_LIMITS = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=10,
    keepalive_expiry=30,
)

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=_TIMEOUT, limits=_LIMITS)
    return _client


async def shutdown_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


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

    print(f"[proxy] POST {url}")
    client = _get_client()
    response = await client.post(url, json=body, headers=headers)
    if response.status_code != 200:
        print(f"[proxy] Upstream returned {response.status_code}: {response.text[:200]}")
    else:
        print(f"[proxy] Upstream 200 OK")
    return response


async def stream_forward(
    path: str,
    body: dict[str, Any],
    incoming_headers: dict[str, str],
) -> AsyncGenerator[bytes, None]:
    headers = _build_headers(incoming_headers)
    url = f"{config.anthropic_api_url}{path}"

    print(f"[proxy] STREAM {url}")
    client = _get_client()
    async with client.stream("POST", url, json=body, headers=headers) as response:
        if response.status_code != 200:
            error_body = await response.aread()
            print(f"[proxy] Upstream stream error {response.status_code}: {error_body[:300]}")
            yield error_body
            return
        async for chunk in response.aiter_bytes():
            yield chunk
    print(f"[proxy] Stream completed")
