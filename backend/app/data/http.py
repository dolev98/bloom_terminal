"""One shared httpx.AsyncClient with the SEC-style User-Agent. Providers use `get_client()`."""

from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


def default_user_agent(settings) -> str:
    ua = (settings.sec_user_agent or "").strip()
    return ua or f"{settings.app_name}/0.1 (personal research terminal)"


def get_client(settings=None) -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        if settings is None:
            from app.core.config import get_settings

            settings = get_settings()
        _client = httpx.AsyncClient(
            headers={"User-Agent": default_user_agent(settings), "Accept-Encoding": "gzip, deflate"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            http2=False,
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
