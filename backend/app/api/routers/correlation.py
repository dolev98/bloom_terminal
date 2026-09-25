from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.analytics.align import AlignmentError
from app.analytics.regimes import PRESETS
from app.correlation import service

router = APIRouter(prefix="/api/correlation", tags=["correlation"])


def _split(s: str | None) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


# --- pair catalog -------------------------------------------------------------------------------
@router.get("/pairs")
async def pairs(country: str | None = None) -> dict:
    await service.ensure_seed_pairs()
    items = await service.list_pairs(country)
    return {"count": len(items), "items": items}


class PairIn(BaseModel):
    id: str | None = None
    name: str | None = None
    a: str
    b: str
    transform_a: str | None = None
    transform_b: str | None = None
    freq: str = "auto"
    lag_b: int = 0
    rationale: str = ""
    tags: list[str] = Field(default_factory=list)
    country: str | None = None


@router.post("/pairs")
async def create_pair(req: PairIn) -> dict:
    try:
        await service.resolve_info(req.a)
        await service.resolve_info(req.b)
    except service.UnknownSeries as e:
        raise HTTPException(400, str(e)) from e
    return await service.save_pair(req.model_dump())


@router.delete("/pairs/{pair_id}")
async def delete_pair(pair_id: str) -> dict:
    if not await service.delete_pair(pair_id):
        raise HTTPException(404, "not found")
    return {"deleted": True}


# --- pair analysis ------------------------------------------------------------------------------------
@router.get("/pair")
async def pair(
    a: str,
    b: str,
    transform_a: str | None = None,
    transform_b: str | None = None,
    freq: str = "auto",
    window: int | None = Query(None, ge=5, le=2000),
    lag_b: int = Query(0, ge=-250, le=250),
    start: date | None = None,
    end: date | None = None,
    methods: str | None = Query(
        None, description="comma list of rolling,leadlag,beta,stationarity,coint,granger"
    ),
    pub_lag: bool = False,
    invert_b: bool = False,
    regime: str | None = Query(
        None, description="preset id from /regimes/presets, or JSON {kind:'windows', params:{windows:[...]}}"
    ),
    max_lag: int | None = Query(None, ge=1, le=60),
    z_window: int | None = Query(None, ge=10, le=1000),
    min_overlap: int = Query(60, ge=10, le=5000),
) -> dict:
    regime_arg: str | dict | None = regime
    if regime and regime.strip().startswith("{"):
        try:
            regime_arg = json.loads(regime)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"bad regime JSON: {e}") from e
    try:
        return await service.analyze_pair(
            a,
            b,
            transform_a=transform_a,
            transform_b=transform_b,
            freq=freq,
            window=window,
            lag_b=lag_b,
            start=start,
            end=end,
            methods=_split(methods) or None,
            pub_lag=pub_lag,
            invert_b=invert_b,
            regime=regime_arg,
            max_lag=max_lag,
            z_window=z_window,
            min_overlap=min_overlap,
        )
    except service.UnknownSeries as e:
        raise HTTPException(404, str(e)) from e
    except AlignmentError as e:
        raise HTTPException(422, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# --- matrix / discover ----------------------------------------------------------------------------------
@router.get("/matrix")
async def matrix(
    ids: str | None = Query(None, description="comma list of series ids / tickers; omit to use `universe`"),
    universe: str = Query("catalog", description="catalog | watchlist"),
    window: int = Query(730, ge=30, le=20000, description="lookback in calendar days"),
    freq: str = Query("1d"),
    shrink: bool = False,
    method: str = Query("pearson", pattern="^(pearson|spearman)$"),
    category: str | None = None,
    country: str | None = None,
    tag: str | None = None,
    transform: str | None = Query(
        None, description="force one transform for every series (default: each series' own)"
    ),
    min_overlap: int = Query(60, ge=10, le=5000),
    live: bool = Query(False, description="bypass the nightly cache"),
) -> dict:
    explicit = _split(ids)
    filtered = bool(category or country or tag or transform or shrink or method != "pearson")
    if not explicit and not filtered and not live and window == 730 and freq == "1d":
        cached = service.read_matrix_cache(universe)
        if cached:
            return cached
    series_ids = explicit or await service.universe_ids(universe, category=category, country=country, tag=tag)
    if len(series_ids) < 2:
        raise HTTPException(422, f"need at least 2 series with stored data (got {len(series_ids)})")
    try:
        return await service.matrix(
            series_ids,
            window_days=window,
            freq=freq,
            shrink=shrink,
            method=method,
            min_overlap=min_overlap,
            transform=transform,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/discover")
async def discover(
    x: str,
    universe: str = Query("catalog", description="catalog | watchlist | comma list of ids"),
    window: int = Query(730, ge=30, le=20000, description="lookback in calendar days"),
    freq: str = Query("1d"),
    roll: int | None = Query(
        None, ge=5, le=2000, description="rolling window (periods) for the stability score"
    ),
    limit: int = Query(50, ge=1, le=500),
    category: str | None = None,
    country: str | None = None,
    live: bool = False,
) -> dict:
    uni: str | list[str] = universe
    if "," in universe or (universe not in ("catalog", "watchlist")):
        uni = _split(universe)
    if (
        isinstance(uni, str)
        and not live
        and window == 730
        and freq == "1d"
        and roll is None
        and not (category or country)
    ):
        cached = service.read_discover_cache(uni, x, limit)
        if cached:
            return cached
    try:
        return await service.discover(
            x,
            uni,
            window_days=window,
            freq=freq,
            window=roll,
            limit=limit,
            category=category,
            country=country,
        )
    except service.UnknownSeries as e:
        raise HTTPException(404, str(e)) from e
    except AlignmentError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/regimes/presets")
async def regime_presets() -> list[dict]:
    return PRESETS


@router.post("/precompute")
async def precompute(
    universe: str | None = Query(None, description="catalog | watchlist; omit for both"),
) -> list[dict]:
    from app.jobs.tasks.correlation import precompute_correlation_job

    return await precompute_correlation_job([universe] if universe else None)
