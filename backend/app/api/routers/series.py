from __future__ import annotations

import io
import re
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.analytics.transforms import resample, transform
from app.data.catalog.loader import get_meta, get_spec
from app.data.freshness import freshness
from app.data.providers.base import SeriesSpec
from app.data.series_service import read_series, refresh_series

router = APIRouter(prefix="/api/series", tags=["series"])


@router.get("/{series_id:path}/observations")
async def observations(
    series_id: str,
    start: date | None = None,
    end: date | None = None,
    transform_kind: str | None = Query(None, alias="transform"),
    freq: str | None = None,
    limit: int = Query(20000, le=200000),
) -> dict:
    spec = await get_spec(series_id)
    if spec is None:
        raise HTTPException(404, f"unknown series {series_id}")
    df = read_series(series_id, start, end)
    if freq:
        df = resample(df, freq, how="mean" if spec.value_kind in ("yield", "spread") else "last")
    if transform_kind and transform_kind != "level":
        df = transform(df, transform_kind, freq or spec.freq)
    if df.height > limit:
        df = df.tail(limit)
    meta = await get_meta(series_id)
    if meta is not None:
        fr = freshness(spec, meta)
        meta = {**meta, "freshness": fr["state"], "expected_by": fr["expected_by"]}
    return {
        "series_id": series_id,
        "name": spec.name,
        "unit": spec.unit,
        "freq": spec.freq,
        "transform": transform_kind or "level",
        "n": df.height,
        "ts": [t.isoformat() for t in df["ts"].to_list()],
        "value": df["value"].to_list(),
        "meta": meta,
        "attribution": _attribution(spec),
        "spec": spec.model_dump(mode="json"),
    }


@router.get("/{series_id:path}/export.csv")
async def export_csv(series_id: str, transform_kind: str | None = Query(None, alias="transform")):
    spec = await get_spec(series_id)
    if spec is None:
        raise HTTPException(404, f"unknown series {series_id}")
    df = read_series(series_id)
    if transform_kind and transform_kind != "level":
        df = transform(df, transform_kind, spec.freq)
    buf = io.StringIO()
    df.write_csv(buf)
    safe = series_id.replace(":", "_").replace("/", "_")
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe}.csv"'},
    )


@router.post("/{series_id:path}/refresh")
async def refresh(series_id: str, full: bool = False) -> dict:
    spec = await get_spec(series_id)
    if spec is None:
        raise HTTPException(404, f"unknown series {series_id}")
    return await refresh_series(series_id, full=full)


_FORMULA_REF = re.compile(r"\b([a-z_]+):[A-Za-z0-9_./^=-]+")


def _attribution(spec: SeriesSpec) -> str | None:
    """Provider attribution; a derived series carries the attributions of the sources in its formula."""
    from app.data.registry import get_registry

    reg = get_registry()
    providers = [spec.provider]
    if spec.formula:
        providers = list(dict.fromkeys(m.group(1) for m in _FORMULA_REF.finditer(spec.formula)))
    out: list[str] = []
    for pid in providers:
        try:
            a = reg.get(pid).license.attribution
        except Exception:
            a = None
        if a and a not in out:
            out.append(a)
    return " ".join(out) or None
