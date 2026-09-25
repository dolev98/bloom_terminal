from __future__ import annotations

import logging

from app.data.store.backup import run_backup
from app.jobs import scheduler as sch

log = logging.getLogger(__name__)


async def backup_job() -> dict:
    return run_backup()


async def cache_purge_job() -> str:
    from app.state import get_cache

    n = await get_cache().purge_expired()
    return f"purged {n}"


async def catch_up_job() -> str:
    """Runs every minute. If the event loop was frozen (laptop asleep), refresh stale series right away."""
    gap = sch.mark_tick()
    if gap > sch.WAKE_GAP_S:
        log.info("wake detected after %.0f s; running catch-up", gap)
        from app.data.series_service import refresh_stale

        res = await refresh_stale()
        try:
            from app.brief.service import brief_job

            await brief_job()
        except Exception as e:  # pragma: no cover
            log.warning("brief after wake failed: %s", e)
        return f"wake after {int(gap)}s: refreshed {len(res)}"
    return ""
