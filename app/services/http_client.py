"""Shared httpx.AsyncClient for connection pooling."""

from __future__ import annotations

from typing import Optional

import httpx


_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("HTTP client not initialized (lifespan)")
    return _client


async def start_client() -> None:
    global _client
    if _client is not None:
        return
    _client = httpx.AsyncClient(
        timeout=httpx.Timeout(600.0, connect=30.0),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=30),
    )


async def stop_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
