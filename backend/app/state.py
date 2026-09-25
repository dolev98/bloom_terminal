"""Process-wide singletons (cache, registry accessors) to avoid circular imports."""

from __future__ import annotations

from app.data.cache import Cache

_cache: Cache | None = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache
