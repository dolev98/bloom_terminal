"""APScheduler 3.x inside the FastAPI lifespan. Jobs log to `job_runs`; a catch-up job runs on startup/wake."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings
from app.data.store.models import JobRun
from app.data.store.sqlite import session_scope

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_running: dict[str, datetime] = {}
_last_tick: float = 0.0
WAKE_GAP_S = 20 * 60  # if the loop was frozen for longer than this, assume the laptop slept


def wrap(job_id: str, fn: Callable[[], Awaitable[object]]) -> Callable[[], Awaitable[None]]:
    async def runner() -> None:
        if job_id in _running:
            log.info("job %s already running, skipping", job_id)
            return
        _running[job_id] = datetime.now(UTC).replace(tzinfo=None)
        t0 = time.perf_counter()
        run_id: int | None = None
        try:
            async with session_scope() as s:
                jr = JobRun(job_id=job_id)
                s.add(jr)
                await s.flush()
                run_id = jr.id
            result = await fn()
            msg = _summarize(result)
            status = "ok"
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"[:1000]
            status = "error"
            log.exception("job %s failed", job_id)
        finally:
            _running.pop(job_id, None)
        dur = time.perf_counter() - t0
        if run_id is not None:
            async with session_scope() as s:
                jr = await s.get(JobRun, run_id)
                if jr:
                    jr.finished_at = datetime.now(UTC).replace(tzinfo=None)
                    jr.status = status
                    jr.message = msg
                    jr.duration_s = round(dur, 2)

    return runner


def _summarize(result: object) -> str:
    if result is None:
        return ""
    if isinstance(result, list):
        ok = sum(1 for r in result if isinstance(r, dict) and r.get("status") == "ok")
        err = sum(1 for r in result if isinstance(r, dict) and r.get("status") == "error")
        return f"{len(result)} items: {ok} ok, {err} error"
    return str(result)[:1000]


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(
            timezone=get_settings().timezone,
            job_defaults={"coalesce": True, "misfire_grace_time": 3600, "max_instances": 1},
        )
    return _scheduler


def job_table() -> list[tuple[str, str, object, object]]:
    """(job_id, name, trigger, coroutine function). Modules append here via the integrator."""
    from app.jobs.tasks import alerts, correlation, maintenance, market, series, valuation

    jobs: list[tuple[str, str, object, object]] = [
        (
            "series.refresh_stale",
            "Refresh stale series",
            IntervalTrigger(minutes=30),
            series.refresh_stale_job,
        ),
        (
            "series.refresh_daily_full",
            "Daily series refresh",
            CronTrigger(hour=1, minute=15),
            series.refresh_daily_job,
        ),
        ("maintenance.backup", "Nightly backup", CronTrigger(hour=3, minute=0), maintenance.backup_job),
        (
            "maintenance.cache_purge",
            "Purge expired cache",
            IntervalTrigger(hours=6),
            maintenance.cache_purge_job,
        ),
        (
            "maintenance.catch_up",
            "Wake detector / catch-up",
            IntervalTrigger(seconds=60),
            maintenance.catch_up_job,
        ),
        (
            "market.quotes_poll",
            "Quotes poll (market hours)",
            IntervalTrigger(seconds=60),
            market.quotes_poll_job,
        ),
        ("market.ohlcv_eod", "EOD OHLCV refresh", CronTrigger(hour=0, minute=30), market.ohlcv_eod_job),
        (
            "correlation.precompute",
            "Correlation precompute",
            CronTrigger(hour=2, minute=0),
            correlation.precompute_correlation_job,
        ),
        ("alerts.evaluate", "Alerts evaluation", IntervalTrigger(seconds=60), alerts.alerts_evaluate_job),
        (
            "alerts.digest_flush",
            "Alerts digest flush",
            CronTrigger(hour=9, minute=0),
            alerts.alerts_digest_flush_job,
        ),
        (
            "valuation.daily",
            "Daily valuation (watchlist)",
            CronTrigger(hour=23, minute=30),
            valuation.valuation_daily_job,
        ),
        (
            "valuation.macro",
            "Valuation macro inputs (rf/ERP/industry)",
            CronTrigger(hour=7, minute=30),
            valuation.valuation_macro_job,
        ),
    ]
    for loader in _EXTRA_JOB_LOADERS:
        try:
            jobs.extend(loader())
        except Exception as e:  # pragma: no cover
            log.warning("job loader failed: %s", e)
    return jobs


_EXTRA_JOB_LOADERS: list = []


def register_job_loader(fn) -> None:
    """Modules register a zero-arg function returning job tuples (called at scheduler start)."""
    _EXTRA_JOB_LOADERS.append(fn)


def register_jobs() -> AsyncIOScheduler:
    sch = get_scheduler()
    for job_id, name, trigger, fn in job_table():
        sch.add_job(wrap(job_id, fn), trigger, id=job_id, name=name, replace_existing=True)
    return sch


async def run_job_now(job_id: str) -> bool:
    sch = get_scheduler()
    job = sch.get_job(job_id)
    if job is None:
        return False
    asyncio.create_task(job.func())
    return True


def jobs_snapshot() -> list[dict]:
    sch = get_scheduler()
    out = []
    for j in sch.get_jobs():
        out.append(
            {
                "id": j.id,
                "name": j.name,
                "next_run": j.next_run_time.isoformat() if j.next_run_time else None,
                "trigger": str(j.trigger),
                "running": j.id in _running,
            }
        )
    return out


def mark_tick() -> float:
    """Called every minute by the catch-up job; returns seconds since the previous tick."""
    global _last_tick
    now = time.monotonic()
    gap = now - _last_tick if _last_tick else 0.0
    _last_tick = now
    return gap
