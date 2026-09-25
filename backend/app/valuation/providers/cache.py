"""Small helpers around the valuation_macro_cache table (provenance-carrying numbers with an as_of)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.data.store.sqlite import session_scope
from app.valuation.orm import MacroCache


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


async def get_cached(key: str, max_age: timedelta | None = None) -> dict | None:
    async with session_scope() as s:
        row = await s.get(MacroCache, key)
    if row is None:
        return None
    if max_age is not None and row.fetched_at and _now() - row.fetched_at > max_age:
        return None
    return {
        "value": row.value,
        "as_of": row.as_of.isoformat() if row.as_of else None,
        "source": row.source,
        "url": row.source_url,
        "note": row.note,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        "cached": True,
    }


async def put_cached(
    key: str, value: float, as_of: date | None, source: str, url: str = "", note: str | None = None
) -> dict:
    async with session_scope() as s:
        row = await s.get(MacroCache, key)
        if row is None:
            s.add(
                MacroCache(
                    key=key,
                    value=value,
                    as_of=as_of,
                    source=source,
                    source_url=url,
                    note=note,
                    fetched_at=_now(),
                )
            )
        else:
            row.value, row.as_of, row.source, row.source_url, row.note, row.fetched_at = (
                value,
                as_of,
                source,
                url,
                note,
                _now(),
            )
    return {
        "value": value,
        "as_of": as_of.isoformat() if as_of else None,
        "source": source,
        "url": url,
        "note": note,
        "cached": False,
    }


async def all_cached() -> dict[str, dict]:
    from sqlalchemy import select

    async with session_scope() as s:
        rows = (await s.execute(select(MacroCache))).scalars().all()
    return {
        r.key: {
            "value": r.value,
            "as_of": r.as_of.isoformat() if r.as_of else None,
            "source": r.source,
            "url": r.source_url,
            "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
        }
        for r in rows
    }
