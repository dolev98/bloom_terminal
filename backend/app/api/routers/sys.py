from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from app.alerts.channels.telegram import get_updates_chat_ids, send_telegram
from app.core.config import get_settings
from app.core.secrets import SECRET_FIELDS, mask, set_secret
from app.data.catalog.loader import list_meta, list_specs
from app.data.registry import get_registry
from app.data.store.backup import list_backups, run_backup
from app.data.store.models import FetchLog, JobRun, Pref
from app.data.store.sqlite import session_scope
from app.jobs.scheduler import jobs_snapshot, run_job_now
from app.state import get_cache

router = APIRouter(prefix="/api/sys", tags=["sys"])


@router.get("/jobs")
async def jobs() -> dict:
    async with session_scope() as s:
        rows = (await s.execute(select(JobRun).order_by(desc(JobRun.id)).limit(50))).scalars().all()
        # latest run and latest successful run per job (the 50 recent runs are dominated by per-minute jobs)
        last_ids = (await s.execute(select(func.max(JobRun.id)).group_by(JobRun.job_id))).scalars().all()
        last_rows = (
            (await s.execute(select(JobRun).where(JobRun.id.in_(last_ids)))).scalars().all()
            if last_ids
            else []
        )
        ok_rows = (
            await s.execute(
                select(JobRun.job_id, func.max(JobRun.finished_at))
                .where(JobRun.status == "ok")
                .group_by(JobRun.job_id)
            )
        ).all()
        # "did something": jobs return "" when they had nothing to do (e.g. quotes poll while markets are closed)
        work_rows = (
            await s.execute(
                select(JobRun.job_id, func.max(JobRun.finished_at))
                .where(JobRun.status == "ok", JobRun.message.is_not(None), JobRun.message != "")
                .group_by(JobRun.job_id)
            )
        ).all()
    runs = [{"id": r.id, "job_id": r.job_id, **_run(r)} for r in rows]
    last = {r.job_id: _run(r) for r in last_rows}
    last_ok = dict(ok_rows)
    last_work = dict(work_rows)
    scheduled = [
        {
            **j,
            "last_run": last.get(j["id"]),
            "last_ok_at": last_ok.get(j["id"]),
            "last_work_at": last_work.get(j["id"]),
        }
        for j in jobs_snapshot()
    ]
    return {"scheduled": scheduled, "runs": runs}


def _run(r: JobRun) -> dict:
    return {
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "status": r.status,
        "message": r.message,
        "duration_s": r.duration_s,
    }


@router.post("/jobs/{job_id}/run")
async def run_job(job_id: str) -> dict:
    if not await run_job_now(job_id):
        raise HTTPException(404, "unknown job")
    return {"started": True}


@router.get("/quotas")
async def quotas() -> dict:
    reg = get_registry()
    cache = get_cache()
    return {"limiters": reg.limiters.snapshot(), "cache": {"hits": cache.hits, "misses": cache.misses}}


@router.get("/fetch-log")
async def fetch_log(limit: int = 100, provider: str | None = None) -> list[dict]:
    async with session_scope() as s:
        stmt = select(FetchLog).order_by(desc(FetchLog.id)).limit(limit)
        if provider:
            stmt = stmt.where(FetchLog.provider == provider)
        rows = (await s.execute(stmt)).scalars().all()
    return [
        {
            "ts": r.ts,
            "provider": r.provider,
            "op": r.op,
            "key": r.key,
            "status": r.status,
            "duration_ms": r.duration_ms,
            "rows": r.rows,
            "error": r.error,
        }
        for r in rows
    ]


@router.get("/freshness")
async def freshness() -> list[dict]:
    from app.data.freshness import freshness as data_freshness
    from app.data.series_service import is_stale

    specs = await list_specs()
    metas = await list_meta()
    out = []
    for sp in specs:
        m = metas.get(sp.series_id)
        fr = data_freshness(sp, m)
        out.append(
            {
                "series_id": sp.series_id,
                "name": sp.name,
                "provider": sp.provider,
                "freq": sp.freq,
                "enabled": sp.enabled,
                "stale": await is_stale(sp, m),  # a re-fetch is due (fetch-time based)
                "freshness": fr["state"],  # the data itself: ok | late | failed | never | paused
                "expected_by": fr["expected_by"],
                **{k: v for k, v in (m or {}).items()},
            }
        )
    return out


@router.get("/llm")
async def llm_usage() -> dict:
    from app.core import budget as _b
    from app.llm.client import configured

    out = await _b.summary()
    out["configured"] = configured()
    return out


@router.get("/backups")
async def backups() -> list[dict]:
    return list_backups()


@router.post("/backups/run")
async def backups_run() -> dict:
    return run_backup()


class TestAlert(BaseModel):
    text: str = "Terminal test alert ✅"
    ops: bool = False


@router.post("/alerts/test")
async def test_alert(req: TestAlert) -> dict:
    ok = await send_telegram(req.text, ops=req.ops)
    return {"sent": ok}


@router.get("/telegram/chats")
async def telegram_chats() -> list[dict]:
    return await get_updates_chat_ids()


# --- settings & prefs -------------------------------------------------------
@router.get("/settings")
async def get_settings_view() -> dict:
    s = get_settings()
    out = {
        "data_dir": str(s.data_dir),
        "timezone": s.timezone,
        "grey_sources_enabled": get_registry().grey_enabled,
        "llm_monthly_budget_usd": s.llm_monthly_budget_usd,
        "secrets": {},
    }
    for f in SECRET_FIELDS:
        val = getattr(s, f, "")
        out["secrets"][f] = {"set": bool(val), "masked": mask(val), "from_keyring": f in s.secrets_overridden}
    return out


class SecretUpdate(BaseModel):
    name: str
    value: str


@router.put("/settings/secret")
async def put_secret(req: SecretUpdate) -> dict:
    if req.name not in SECRET_FIELDS:
        raise HTTPException(400, "unknown secret")
    s = get_settings()
    setattr(s, req.name, req.value)
    stored = set_secret(req.name, req.value)
    # propagate to live providers
    reg = get_registry()
    for p in reg.all():
        if hasattr(p, "api_key") and req.name == f"{p.id}_api_key":
            p.api_key = req.value  # type: ignore[attr-defined]
    return {"saved": True, "keyring": stored}


@router.get("/prefs")
async def get_prefs() -> dict:
    async with session_scope() as s:
        rows = (await s.execute(select(Pref))).scalars().all()
    prefs = {r.key: r.value for r in rows}
    prefs.setdefault("grey_sources_enabled", get_registry().grey_enabled)
    return prefs


class PrefUpdate(BaseModel):
    key: str
    value: object


@router.put("/prefs")
async def put_pref(req: PrefUpdate) -> dict:
    async with session_scope() as s:
        row = await s.get(Pref, req.key)
        if row is None:
            s.add(Pref(key=req.key, value=req.value))
        else:
            row.value = req.value
    if req.key == "grey_sources_enabled":
        get_registry().grey_enabled = bool(req.value)
    return {"saved": True}
