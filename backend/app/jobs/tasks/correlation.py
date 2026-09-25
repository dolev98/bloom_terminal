"""Nightly correlation precompute: clustered matrices + all-pairs discovery rankings for the catalog (daily
series) and watchlist (closes) universes, written to settings.derived_dir/correlation/*.parquet.

Suggested registration (scheduler.register_jobs):
    sch.add_job(wrap("correlation.precompute", correlation.precompute_correlation_job),
                CronTrigger(hour=2, minute=0), id="correlation.precompute", name="Correlation precompute", replace_existing=True)
"""

from __future__ import annotations

import logging

from app.correlation import service

log = logging.getLogger(__name__)
UNIVERSES = ("catalog", "watchlist")


async def precompute_correlation_job(
    universes: list[str] | None = None, window_days: int = 730, freq: str = "1d"
) -> list[dict]:
    """Idempotent: recomputes and atomically replaces the cache files for each universe. Safe after sleep."""
    await service.ensure_seed_pairs()
    out: list[dict] = []
    for u in universes or UNIVERSES:
        try:
            res = await service.precompute_universe(u, window_days=window_days, freq=freq)
        except Exception as e:  # one universe failing must not block the other
            log.exception("correlation precompute for %s failed", u)
            res = {"universe": u, "status": "error", "error": f"{type(e).__name__}: {e}"[:500]}
        out.append(res)
    return out
