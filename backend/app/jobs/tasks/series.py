from __future__ import annotations

from app.data.catalog.loader import list_specs
from app.data.series_service import refresh_series, refresh_stale


async def refresh_stale_job() -> list[dict]:
    return await refresh_stale()


async def refresh_daily_job() -> list[dict]:
    specs = await list_specs(enabled_only=True)
    out = []
    # providers first, formula (derived) series last so their inputs are fresh
    for sp in sorted(specs, key=lambda sp: bool(sp.formula)):
        out.append(await refresh_series(sp.series_id))
    return out
