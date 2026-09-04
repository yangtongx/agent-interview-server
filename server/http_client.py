from __future__ import annotations

import httpx

_client: httpx.Client | None = None
_async_client: httpx.AsyncClient | None = None

_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
_LIMITS = httpx.Limits(max_connections=32, max_keepalive_connections=16)


def http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=_TIMEOUT, follow_redirects=False, limits=_LIMITS)
    return _client


def async_http() -> httpx.AsyncClient:
    global _async_client
    if _async_client is None:
        _async_client = httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False, limits=_LIMITS)
    return _async_client


def close_http() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


async def close_async_http() -> None:
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
