"""Two-tier response cache: in-memory TTL + SQLite `response_cache` (keyed by request hash)."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from sqlalchemy import delete, select

from app.data.store.models import ResponseCache
from app.data.store.sqlite import session_scope


def make_key(provider: str, op: str, params: dict[str, Any]) -> str:
    payload = json.dumps({"p": provider, "op": op, "a": params}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class Cache:
    def __init__(self):
        self._mem: dict[str, tuple[float, Any]] = {}
        self.hits = 0
        self.misses = 0

    async def get(self, key: str) -> Any | None:
        now = time.time()
        hit = self._mem.get(key)
        if hit and hit[0] > now:
            self.hits += 1
            return hit[1]
        async with session_scope() as s:
            row = await s.get(ResponseCache, key)
            if row and row.expires_at > now:
                self._mem[key] = (row.expires_at, json.loads(row.payload))
                self.hits += 1
                return self._mem[key][1]
        self.misses += 1
        return None

    async def put(self, key: str, value: Any, ttl_s: float, provider: str = "", op: str = "") -> None:
        expires = time.time() + ttl_s
        self._mem[key] = (expires, value)
        async with session_scope() as s:
            row = await s.get(ResponseCache, key)
            payload = json.dumps(value, default=str)
            if row:
                row.payload = payload
                row.expires_at = expires
            else:
                s.add(ResponseCache(key=key, provider=provider, op=op, payload=payload, expires_at=expires))

    async def purge_expired(self) -> int:
        now = time.time()
        self._mem = {k: v for k, v in self._mem.items() if v[0] > now}
        async with session_scope() as s:
            res = await s.execute(delete(ResponseCache).where(ResponseCache.expires_at <= now))
            return res.rowcount or 0

    async def count(self) -> int:
        async with session_scope() as s:
            rows = (await s.execute(select(ResponseCache.key))).all()
            return len(rows)
