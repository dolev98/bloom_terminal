from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.data.catalog.loader import delete_spec, get_meta, get_spec, list_meta, list_specs, upsert_spec
from app.data.freshness import freshness, last_value
from app.data.providers.base import Capability, NotSupported, SeriesSpec
from app.data.providers.manual import parse_csv_observations
from app.data.registry import get_registry
from app.data.series_service import read_series, refresh_series, write_manual

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("")
async def list_catalog(
    provider: str | None = None, country: str | None = None, category: str | None = None, q: str | None = None
) -> dict:
    specs = await list_specs(provider=provider, country=country, category=category, q=q)
    metas = await list_meta()
    items = []
    for sp in specs:
        m = metas.get(sp.series_id, {})
        fr = freshness(sp, m)
        items.append(
            {
                **sp.model_dump(),
                "meta": {
                    **{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in m.items()},
                    "last_value": last_value(sp.series_id) if m.get("n_obs") else None,
                    "freshness": fr["state"],
                    "expected_by": fr["expected_by"],
                },
            }
        )
    return {"count": len(items), "items": items}


@router.get("/providers")
async def providers() -> list[dict]:
    return get_registry().status()


class ResolveRequest(BaseModel):
    text: str  # URL or provider:key


@router.post("/resolve")
async def resolve(req: ResolveRequest) -> dict:
    """Turn a pasted URL / id into a proposed SeriesSpec via provider.parse_url + describe()."""
    reg = get_registry()
    text = req.text.strip()
    provider = None
    key: str | None = None
    if "://" in text:
        for p in reg.all():
            k = p.parse_url(text)
            if k:
                provider, key = p, k
                break
        if provider is None:
            raise HTTPException(400, "no provider recognises this URL")
    else:
        try:
            provider, key, fld = reg.resolve(text)
            if fld:
                key = f"{key}:{fld}"
        except Exception as e:
            raise HTTPException(400, f"cannot parse id: {e}") from e
    ok, why = reg.is_usable(provider)
    if not ok:
        raise HTTPException(400, f"{provider.id}: {why}")
    assert key is not None
    try:
        spec = await reg.call(provider, "describe", lambda: provider.describe(key), key=key)
    except NotSupported:
        spec = SeriesSpec(series_id=f"{provider.id}:{key}", provider=provider.id, provider_key=key, name=key)
    return spec.model_dump()


@router.get("/search")
async def search(q: str, provider: str | None = None) -> list[dict]:
    reg = get_registry()
    out: list[dict] = []
    for p in reg.for_capability(Capability.SEARCH):
        if provider and p.id != provider:
            continue
        if not reg.is_usable(p)[0]:
            continue
        try:
            res = await reg.call(p, "search", lambda p=p: p.search(q), key=q)
            out.extend(s.model_dump() for s in res)
        except Exception:
            continue
    return out


class TestFetchRequest(BaseModel):
    spec: SeriesSpec


@router.post("/test-fetch")
async def test_fetch(req: TestFetchRequest) -> dict:
    reg = get_registry()
    p = reg.get(req.spec.provider)
    ok, why = reg.is_usable(p)
    if not ok:
        raise HTTPException(400, f"{p.id}: {why}")
    df = await reg.call(p, "get_series", lambda: p.get_series(req.spec), key=req.spec.series_id)
    tail = df.tail(200)
    return {
        "n": df.height,
        "first_ts": df["ts"].min().isoformat() if df.height else None,
        "last_ts": df["ts"].max().isoformat() if df.height else None,
        "ts": [t.isoformat() for t in tail["ts"].to_list()],
        "value": tail["value"].to_list(),
    }


@router.put("/{series_id:path}")
async def put_series(series_id: str, spec: SeriesSpec, refresh: bool = True) -> dict:
    if spec.series_id != series_id:
        raise HTTPException(400, "series_id mismatch")
    await upsert_spec(spec)
    result = await refresh_series(series_id, full=True) if refresh and not spec.provider == "manual" else None
    return {"saved": True, "refresh": result}


@router.delete("/{series_id:path}")
async def delete_series(series_id: str) -> dict:
    ok = await delete_spec(series_id)
    if not ok:
        raise HTTPException(404, "not found")
    return {"deleted": True}


class ManualData(BaseModel):
    csv: str
    replace: bool = False


@router.post("/{series_id:path}/manual")
async def manual_upload(series_id: str, data: ManualData) -> dict:
    spec = await get_spec(series_id)
    if spec is None:
        raise HTTPException(404, "not found")
    df = parse_csv_observations(data.csv)
    if df.is_empty():
        raise HTTPException(400, "no valid 'date,value' rows")
    merged = write_manual(series_id, df, replace=data.replace)
    from app.data.series_service import _update_meta

    await _update_meta(series_id, "ok", None, df.height)
    return {"rows_added": df.height, "n": merged.height}


@router.get("/{series_id:path}/meta")
async def meta(series_id: str) -> dict:
    m = await get_meta(series_id)
    if m is None:
        raise HTTPException(404, "not found")
    m["has_data"] = read_series(series_id).height > 0
    return m
