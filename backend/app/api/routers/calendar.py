from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from app.calendar import service

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


def _csv(v: str | None) -> list[str] | None:
    return [x.strip() for x in v.split(",") if x.strip()] if v else None


@router.get("/events")
async def events(
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
    countries: str | None = None,
    kinds: str | None = None,
    min_importance: int = 1,
    watchlist_only: bool = False,
    q: str | None = None,
) -> dict:
    start = start or date.today() - timedelta(days=7)
    end = end or start + timedelta(days=30)
    items = await service.list_events(
        start, end, _csv(countries), _csv(kinds), min_importance, watchlist_only, q
    )
    return {"from": start.isoformat(), "to": end.isoformat(), "count": len(items), "items": items}


@router.get("/week")
async def week(start: date | None = None, countries: str | None = None, min_importance: int = 1) -> dict:
    return await service.week(start, _csv(countries), min_importance)


@router.get("/central-banks")
async def central_banks() -> list[dict]:
    return await service.central_banks()


@router.get("/risk-windows")
async def risk_windows() -> list[dict]:
    return await service.risk_windows()


@router.get("/export.ics")
async def export_ics(
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
    countries: str | None = None,
    kinds: str | None = None,
    min_importance: int = 1,
    watchlist_only: bool = False,
):
    start = start or date.today() - timedelta(days=7)
    end = end or start + timedelta(days=60)
    items = await service.list_events(
        start, end, _csv(countries), _csv(kinds), min_importance, watchlist_only
    )
    return Response(
        service.to_ics(items),
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="terminal-calendar.ics"'},
    )


class UserEvent(BaseModel):
    title: str
    release_ts: str
    end_ts: str | None = None
    kind: str = "geo"
    country: str = "GLOBAL"
    category: str = "other"
    importance: int = 2
    event_key: str | None = None
    reference_period: str | None = None
    currency: str | None = None
    linked_series: list[str] = []
    affected_tickers: list[str] = []
    notes: str | None = None
    source_url: str | None = None
    consensus: float | None = None
    actual: float | None = None
    previous: float | None = None
    unit: str | None = None


@router.post("/events")
async def create_event(req: UserEvent) -> dict:
    return await service.create_user_event(req.model_dump())


@router.get("/events/{event_id}")
async def event_detail(event_id: int) -> dict:
    out = await service.get_event(event_id)
    if out is None:
        raise HTTPException(404, "event not found")
    return out


@router.delete("/events/{event_id}")
async def delete_event(event_id: int) -> dict:
    if not await service.delete_event(event_id):
        raise HTTPException(404, "event not found")
    return {"deleted": True}


@router.post("/refresh")
async def refresh(
    scope: str = "all",
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
) -> dict:
    if scope not in ("all", "official", "vendor", "corporate", "actuals"):
        raise HTTPException(400, "scope must be all|official|vendor|corporate|actuals")
    return await service.refresh(scope, start, end)


@router.post("/esi/{country}")
async def rebuild_esi(country: str) -> dict:
    from app.calendar.surprise import build_esi

    return await build_esi(country)
