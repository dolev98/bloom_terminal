"""Series service: fetch -> validate -> Parquet -> meta. Handles derived (formula) series and fallbacks."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import polars as pl

from app.core.config import get_settings
from app.data import derived as derived_mod
from app.data.catalog.loader import get_spec, list_specs
from app.data.providers.base import ProviderError, SeriesSpec, empty_observations
from app.data.registry import get_registry
from app.data.store.models import SeriesMeta
from app.data.store.parquet import ParquetStore
from app.data.store.sqlite import session_scope

log = logging.getLogger(__name__)

# How stale a series may be before a refresh is due (by native frequency).
STALE_AFTER = {"tick": timedelta(minutes=1), "1m": timedelta(minutes=5), "5m": timedelta(minutes=15), "1h": timedelta(hours=1), "1d": timedelta(hours=18), "1w": timedelta(days=6), "1mo": timedelta(days=7), "1q": timedelta(days=14), "1y": timedelta(days=30), "irregular": timedelta(days=1)}
OVERLAP = {"1d": timedelta(days=10), "1w": timedelta(days=30), "1mo": timedelta(days=100), "1q": timedelta(days=400), "1y": timedelta(days=800)}

_store: ParquetStore | None = None


def get_store() -> ParquetStore:
    global _store
    if _store is None:
        _store = ParquetStore(get_settings().parquet_dir)
    return _store


def set_store(store: ParquetStore | None) -> None:
    global _store
    _store = store


def validate_range(spec: SeriesSpec, df: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    if df.is_empty() or (spec.plausible_min is None and spec.plausible_max is None):
        return df, 0
    cond = pl.lit(True)
    if spec.plausible_min is not None:
        cond = cond & (pl.col("value") >= spec.plausible_min)
    if spec.plausible_max is not None:
        cond = cond & (pl.col("value") <= spec.plausible_max)
    kept = df.filter(cond)
    return kept, df.height - kept.height


async def refresh_series(series_id: str, full: bool = False) -> dict:
    spec = await get_spec(series_id)
    if spec is None:
        raise ProviderError(f"series {series_id!r} not in catalog")
    if not spec.enabled:
        return {"series_id": series_id, "status": "skipped", "reason": "disabled"}
    if spec.formula:
        return await refresh_derived(spec)
    reg = get_registry()
    store = get_store()
    provider, _, _ = reg.resolve(series_id)
    since: date | None = None
    if not full:
        existing = store.stats(series_id)
        if existing["last_ts"] is not None:
            since = (existing["last_ts"] - OVERLAP.get(spec.freq, timedelta(days=30))).date()
    status, err, rows, dropped = "ok", None, 0, 0
    try:
        df = await reg.call(provider, "get_series", lambda: provider.get_series(spec, since=since), key=series_id)
        df, dropped = validate_range(spec, df)
        if dropped:
            log.warning("%s: dropped %d implausible values", series_id, dropped)
        if not df.is_empty():
            store.write(series_id, df)
        rows = df.height
    except Exception as e:
        status, err = "error", f"{type(e).__name__}: {e}"[:500]
        log.warning("refresh %s failed: %s", series_id, err)
    await _update_meta(series_id, status, err, rows)
    return {"series_id": series_id, "status": status, "rows": rows, "dropped": dropped, "error": err}


async def refresh_derived(spec: SeriesSpec) -> dict:
    store = get_store()
    ids = derived_mod.formula_inputs(spec.formula or "")
    frames = {fid: store.read(fid) for fid in ids}
    missing = [fid for fid, f in frames.items() if f.is_empty()]
    if missing:
        await _update_meta(spec.series_id, "error", f"missing inputs: {missing}", 0)
        return {"series_id": spec.series_id, "status": "error", "error": f"missing inputs: {missing}"}
    try:
        out = derived_mod.evaluate(spec.formula or "", frames)
        out, dropped = validate_range(spec, out)
        store.write(spec.series_id, out, replace=True)
        await _update_meta(spec.series_id, "ok", None, out.height)
        return {"series_id": spec.series_id, "status": "ok", "rows": out.height, "dropped": dropped}
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:500]
        await _update_meta(spec.series_id, "error", err, 0)
        return {"series_id": spec.series_id, "status": "error", "error": err}


async def _update_meta(series_id: str, status: str, err: str | None, rows: int) -> None:
    stats = get_store().stats(series_id)
    async with session_scope() as s:
        m = await s.get(SeriesMeta, series_id)
        if m is None:
            m = SeriesMeta(series_id=series_id)
            s.add(m)
        m.fetched_at = datetime.now(tz=UTC).replace(tzinfo=None)
        m.last_status = status
        m.last_error = err
        m.rows_last_fetch = rows
        m.n_obs = stats["n"]
        m.first_ts = stats["first_ts"]
        m.last_ts = stats["last_ts"]


async def is_stale(spec: SeriesSpec, meta: dict | None) -> bool:
    if meta is None or meta.get("fetched_at") is None:
        return True
    return datetime.now(UTC).replace(tzinfo=None) - meta["fetched_at"] > STALE_AFTER.get(spec.freq, timedelta(days=1))


async def refresh_stale(limit: int | None = None) -> list[dict]:
    """Refresh every enabled series whose last fetch is older than its cadence; derived series last."""
    from app.data.catalog.loader import list_meta

    specs = await list_specs(enabled_only=True)
    metas = await list_meta()
    results = []
    todo = [sp for sp in specs if not sp.formula and await is_stale(sp, metas.get(sp.series_id))]
    if limit:
        todo = todo[:limit]
    for sp in todo:
        results.append(await refresh_series(sp.series_id))
    for sp in specs:
        if sp.formula:
            results.append(await refresh_derived(sp))
    return results


def read_series(series_id: str, start: date | None = None, end: date | None = None) -> pl.DataFrame:
    return get_store().read(series_id, start, end)


def write_manual(series_id: str, df: pl.DataFrame, replace: bool = False) -> pl.DataFrame:
    if df.is_empty():
        return empty_observations()
    return get_store().write(series_id, df, replace=replace)
