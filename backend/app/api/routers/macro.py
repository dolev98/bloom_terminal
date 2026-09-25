from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.macro import service

router = APIRouter(prefix="/api/macro", tags=["macro"])
KNOWN = {c["cc"] for c in service.countries()}


@router.get("/countries")
async def list_countries() -> list[dict]:
    return service.countries()


@router.get("/compare")
async def compare(indicator: str, countries: str = "US,IL,EA") -> dict:
    ccs = [c.strip().upper() for c in countries.split(",") if c.strip()]
    return await service.compare(indicator, ccs)


@router.get("/heatmap")
async def heatmap(countries: str | None = None, indicators: str | None = None) -> dict:
    ccs = [c.strip().upper() for c in countries.split(",") if c.strip()] if countries else None
    inds = [i.strip() for i in indicators.split(",") if i.strip()] if indicators else None
    return await service.heatmap(ccs, inds)


@router.get("/{cc}")
async def dashboard(cc: str) -> dict:
    if cc.upper() not in KNOWN:
        raise HTTPException(404, f"unknown country {cc}; known: {sorted(KNOWN)}")
    return await service.country_dashboard(cc)


@router.post("/{cc}/refresh")
async def refresh(cc: str, full: bool = False) -> dict:
    if cc.upper() not in KNOWN:
        raise HTTPException(404, f"unknown country {cc}")
    res = await service.refresh_country(cc, full=full)
    return {
        "country": cc.upper(),
        "results": res,
        "ok": sum(1 for r in res if r.get("status") == "ok"),
        "errors": sum(1 for r in res if r.get("status") == "error"),
    }
