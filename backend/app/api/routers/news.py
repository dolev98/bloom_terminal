"""/api/news — clustered feed, cluster detail, poll, filings, sources, stats, rail, enrichment."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.news import enrich, service

router = APIRouter(prefix="/api/news", tags=["news"])


def _csv(v: str | None) -> list[str] | None:
    if not v:
        return None
    return [x.strip() for x in v.split(",") if x.strip()] or None


@router.get("/feed")
async def feed(
    tickers: str | None = None,
    since: str | None = Query(None, description="ISO datetime or relative: 24h, 7d"),
    min_importance: int = 0,
    kinds: str | None = Query(None, description="comma list: filing,wire,news_api,rss"),
    lang: str | None = None,
    event_types: str | None = None,
    q: str | None = None,
    unread_only: bool = False,
    limit: int = Query(100, le=500),
) -> list[dict]:
    return await service.feed(
        tickers=_csv(tickers),
        since=since,
        min_importance=min_importance,
        kinds=_csv(kinds),
        lang=lang,
        limit=limit,
        q=q,
        unread_only=unread_only,
        event_types=_csv(event_types),
    )


@router.get("/clusters/{cluster_id}")
async def cluster(cluster_id: int) -> dict:
    c = await service.cluster_detail(cluster_id)
    if c is None:
        raise HTTPException(404, "unknown cluster")
    return c


@router.post("/poll")
async def poll(tickers: str | None = None, force: bool = True, only: str | None = None) -> dict:
    return await service.poll(_csv(tickers), force=force, only=_csv(only))


@router.get("/filings")
async def filings(
    tickers: str | None = None,
    forms: str | None = None,
    since: str | None = None,
    limit: int = Query(100, le=1000),
) -> list[dict]:
    return await service.filings(_csv(tickers), _csv(forms), limit, since)


@router.get("/sources")
async def sources() -> list[dict]:
    return await service.sources_status()


class SourceUpdate(BaseModel):
    enabled: bool


@router.put("/sources/{source_id}")
async def update_source(source_id: str, req: SourceUpdate) -> dict:
    await service.ensure_sources()
    out = await service.set_source_enabled(source_id, req.enabled)
    if out is None:
        raise HTTPException(404, "unknown source")
    return out


@router.get("/stats")
async def stats(ticker: str, days: int = Query(30, le=730)) -> dict:
    return await service.stats(ticker, days)


@router.get("/rail")
async def rail(tickers: str | None = None, hours: int = Query(24, le=168)) -> list[dict]:
    return await service.rail(_csv(tickers), hours)


class ReadReq(BaseModel):
    ids: list[int]
    read: bool = True


@router.post("/read")
async def mark_read(req: ReadReq) -> dict:
    return {"updated": await service.mark_read(req.ids, req.read)}


@router.post("/enrich/{cluster_id}")
async def enrich_cluster(cluster_id: int) -> dict:
    c = await service.cluster_detail(cluster_id)
    if c is None:
        raise HTTPException(404, "unknown cluster")
    out = await enrich.enrich_cluster(cluster_id, force=True)
    if out is None:
        return {"enriched": False, "reason": "LLM not configured, budget exhausted or call failed"}
    return {"enriched": True, **out}


@router.post("/series/{ticker}")
async def series(ticker: str) -> dict:
    return await service.sentiment_series(ticker)
