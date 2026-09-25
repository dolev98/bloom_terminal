"""Macro service: country dashboards (tiles / regime / recessions / nowcasts / next events), cross-country compare and heatmap."""

from __future__ import annotations

import logging
import math
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import polars as pl
import yaml
from sqlalchemy import select

from app.analytics.transforms import resample, transform
from app.data.catalog.loader import get_spec, list_specs, normalize_seed, upsert_spec
from app.data.providers.base import SeriesSpec
from app.data.series_service import read_series, refresh_series
from app.data.store.sqlite import session_scope
from app.macro.models import MacroMappingOverride

log = logging.getLogger(__name__)
CATALOG_PATH = Path(__file__).with_name("catalog.yaml")
Z_WINDOW = {"1d": 756, "1w": 156, "1mo": 36, "1q": 12, "1y": 3, "irregular": 36}
# Max age (days) of the latest observation before a tile counts as stale. Observations are stamped at the START of
# their period, so a monthly series published ~5 weeks after month-end is legitimately ~95 days old just before the
# next release (e.g. the July trade balance on Oct 5). The spec's publication_lag_days is added on top.
STALE_DAYS = {"1d": 7, "1w": 21, "1mo": 100, "1q": 275, "1y": 550, "irregular": 90}
SPARK = 24
GROWTH_KEYS = ("gdp_yoy", "ip", "retail", "pmi", "payrolls")
INFLATION_KEYS = ("cpi", "core_cpi", "ppi", "wages")


@lru_cache(maxsize=1)
def load_catalog(path: Path = CATALOG_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def countries() -> list[dict]:
    cat = load_catalog()
    return [{"cc": cc, **meta} for cc, meta in (cat.get("countries") or {}).items()]


def _norm_entry(v, default_transform: str) -> tuple[str, str]:
    if isinstance(v, dict):
        return str(v["id"]), str(v.get("transform") or default_transform)
    return str(v), default_transform


def entry_overrides(cc: str | None, indicator: str) -> dict:
    """Per-country display overrides from the catalog mapping entry: `name`, `unit` and `scale`.

    `scale` converts the stored series into the display unit (e.g. jobless claims stored as a count -> thousands,
    the US trade balance stored in $ millions -> $ billions), so `last`/`change`/sparkline match `unit`.
    """
    if not cc:
        return {}
    cat = load_catalog()
    for sec in (((cat.get("mapping") or {}).get(cc.upper())) or {}).values():
        v = (sec or {}).get(indicator)
        if isinstance(v, dict):
            return {k: v[k] for k in ("name", "unit", "scale") if v.get(k) is not None}
        if v is not None:
            return {}
    return {}


async def mapping_for(cc: str) -> dict[str, dict[str, tuple[str, str]]]:
    """{section: {indicator: (series_id, transform)}} with DB overrides applied."""
    cat = load_catalog()
    inds = cat.get("indicators") or {}
    out: dict[str, dict[str, tuple[str, str]]] = {}
    for section, keys in (cat.get("sections") or {}).items():
        sec_map = ((cat.get("mapping") or {}).get(cc.upper()) or {}).get(section) or {}
        out[section] = {}
        for k in keys:
            if k in sec_map:
                out[section][k] = _norm_entry(sec_map[k], (inds.get(k) or {}).get("transform", "level"))
    async with session_scope() as s:
        rows = (
            (await s.execute(select(MacroMappingOverride).where(MacroMappingOverride.country == cc.upper())))
            .scalars()
            .all()
        )
    for r in rows:
        for section, keys in (cat.get("sections") or {}).items():
            if r.indicator in keys:
                out[section][r.indicator] = (
                    r.series_id,
                    r.transform or (inds.get(r.indicator) or {}).get("transform", "level"),
                )
    return out


async def ensure_catalog(force: bool = False) -> int:
    """Add the macro series missing from the catalog (never overwrites existing rows) and disable `retired_series`.

    Idempotent (one SELECT per call)."""
    specs = await list_specs()
    existing = {sp.series_id for sp in specs}
    retired = set(load_catalog().get("retired_series") or [])
    for sp in specs:
        if sp.series_id in retired and sp.enabled:
            await upsert_spec(sp.model_copy(update={"enabled": False}))
            log.info("macro catalog: disabled retired series %s", sp.series_id)
    n = 0
    for item in load_catalog().get("new_series") or []:
        if item["series_id"] in existing:
            continue
        try:
            await upsert_spec(normalize_seed(item))
            n += 1
        except Exception as e:  # pragma: no cover
            log.warning("macro catalog: cannot add %s: %s", item.get("series_id"), e)
    if n:
        log.info("macro catalog: added %d series", n)
    return n


def _z(values: list[float], window: int) -> float | None:
    hist = [v for v in values[-window:] if v is not None and not math.isnan(v)]
    if len(hist) < 6:
        return None
    mean = sum(hist) / len(hist)
    var = sum((x - mean) ** 2 for x in hist) / (len(hist) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0
    return max(-5.0, min(5.0, (hist[-1] - mean) / sd))


def tile_from_frame(
    df: pl.DataFrame,
    spec: SeriesSpec | None,
    indicator: str,
    series_id: str,
    kind: str,
    name: str,
    unit: str,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(UTC).replace(tzinfo=None)
    freq = spec.freq if spec else "1mo"
    base = {
        "indicator": indicator,
        "series_id": series_id,
        "name": name,
        "unit": unit or (spec.unit if spec else ""),
        "transform": kind,
        "freq": freq,
        "last": None,
        "last_ts": None,
        "prev": None,
        "change": None,
        "yoy": None,
        "zscore_3y": None,
        "sparkline": {"ts": [], "value": []},
        "stale": True,
        "n": 0,
    }
    if df.is_empty():
        return base
    x = transform(df.sort("ts"), kind, freq) if kind != "level" else df.sort("ts")
    if x.is_empty():
        return base
    vals = x["value"].to_list()
    last_ts = x["ts"][-1]
    yoy = None
    if kind == "level" and spec and spec.value_kind in ("level_index", "flow", "stock", "price"):
        y = transform(df.sort("ts"), "yoy", freq)
        yoy = float(y["value"][-1]) if y.height else None
    elif kind == "yoy":
        yoy = float(vals[-1])
    tail = x.tail(SPARK)
    return {
        **base,
        "last": float(vals[-1]),
        "last_ts": last_ts,
        "prev": float(vals[-2]) if len(vals) > 1 else None,
        "change": float(vals[-1] - vals[-2]) if len(vals) > 1 else None,
        "yoy": yoy,
        "zscore_3y": _z(vals, Z_WINDOW.get(freq, 36)),
        "sparkline": {"ts": [t.isoformat() for t in tail["ts"].to_list()], "value": tail["value"].to_list()},
        "stale": (now - last_ts).days
        > STALE_DAYS.get(freq, 90) + int((spec.publication_lag_days if spec else 0) or 0),
        "n": x.height,
    }


def _scaled(df: pl.DataFrame, scale: float | None) -> pl.DataFrame:
    if not scale or scale == 1 or df.is_empty():
        return df
    return df.with_columns(pl.col("value") * float(scale))


async def _tile(indicator: str, series_id: str, kind: str, cc: str | None = None) -> dict:
    cat = load_catalog()
    meta = {**((cat.get("indicators") or {}).get(indicator) or {}), **entry_overrides(cc, indicator)}
    spec = await get_spec(series_id)
    df = _scaled(read_series(series_id), meta.get("scale"))
    return tile_from_frame(
        df, spec, indicator, series_id, kind, meta.get("name", indicator), meta.get("unit", "")
    )


def _monthly_z_trail(
    frames: dict[str, tuple[pl.DataFrame, str, str]], keys: tuple[str, ...], months: int = 12
) -> list[dict]:
    """Composite z (mean over indicators) at each of the last `months` month-ends, using a trailing-3y window at each point."""
    monthly: dict[str, pl.DataFrame] = {}
    windows: dict[str, int] = {}
    for k, (df, kind, freq) in frames.items():
        if k not in keys or df.is_empty():
            continue
        x = transform(df.sort("ts"), kind, freq) if kind != "level" else df.sort("ts")
        if x.is_empty():
            continue
        m = (
            resample(x, "1mo", "last")
            if freq in ("1d", "1w")
            else x.with_columns(pl.col("ts").dt.truncate("1mo"))
        )
        monthly[k] = m.sort("ts")
        # 3 years of observations at the series' own frequency — the same window the tiles use
        windows[k] = Z_WINDOW.get(freq, 36) if freq in ("1mo", "1q", "1y") else 36
    if not monthly:
        return []
    end = max(m["ts"].max() for m in monthly.values())
    points = []
    for i in range(months - 1, -1, -1):
        y, mo = divmod(end.year * 12 + end.month - 1 - i, 12)
        t = end.replace(year=y, month=mo + 1, day=1)
        zs = []
        for k, m in monthly.items():
            hist = m.filter(pl.col("ts") <= t)["value"].to_list()
            z = _z(hist, windows[k])
            if z is not None:
                zs.append(z)
        points.append({"ts": t.isoformat()[:10], "z": (sum(zs) / len(zs)) if zs else None, "n": len(zs)})
    return points


def recession_periods_from_flag(df: pl.DataFrame) -> list[dict]:
    out, start = [], None
    for t, v in zip(df.sort("ts")["ts"].to_list(), df.sort("ts")["value"].to_list(), strict=True):
        if v and start is None:
            start = t
        elif not v and start is not None:
            out.append({"start": start.date().isoformat(), "end": t.date().isoformat()})
            start = None
    if start is not None:
        out.append({"start": start.date().isoformat(), "end": None})
    return out


def technical_recessions(gdp_level: pl.DataFrame, freq: str = "1q") -> list[dict]:
    """Two consecutive negative q/q prints -> recession from the first negative quarter until the first positive one."""
    if gdp_level.is_empty():
        return []
    q = transform(gdp_level.sort("ts"), "pct", freq)
    ts, vals = q["ts"].to_list(), q["value"].to_list()
    out, start, neg = [], None, 0
    for t, v in zip(ts, vals, strict=True):
        if v < 0:
            neg += 1
            if neg == 2 and start is None:
                start = ts[ts.index(t) - 1]
        else:
            if start is not None:
                out.append({"start": start.date().isoformat(), "end": t.date().isoformat()})
                start = None
            neg = 0
    if start is not None:
        out.append({"start": start.date().isoformat(), "end": None})
    return out


async def country_dashboard(cc: str) -> dict:
    cc = cc.upper()
    await ensure_catalog()
    cat = load_catalog()
    mp = await mapping_for(cc)
    sections, frames = [], {}
    for section, items in mp.items():
        tiles = []
        for indicator, (sid, kind) in items.items():
            t = await _tile(indicator, sid, kind, cc)
            tiles.append(t)
            spec = await get_spec(sid)
            frames[indicator] = (read_series(sid), kind, spec.freq if spec else "1mo")
        sections.append({"name": section, "tiles": tiles})
    growth = [
        t["zscore_3y"]
        for sec in sections
        for t in sec["tiles"]
        if t["indicator"] in GROWTH_KEYS and t["zscore_3y"] is not None
    ]
    infl = [
        t["zscore_3y"]
        for sec in sections
        for t in sec["tiles"]
        if t["indicator"] in INFLATION_KEYS and t["zscore_3y"] is not None
    ]
    gtrail = _monthly_z_trail(frames, GROWTH_KEYS)
    itrail = _monthly_z_trail(frames, INFLATION_KEYS)
    trail = [
        {
            "ts": g["ts"],
            "growth_z": g["z"],
            "inflation_z": next((i["z"] for i in itrail if i["ts"] == g["ts"]), None),
        }
        for g in gtrail
    ]
    regime = {
        "growth_z": (sum(growth) / len(growth)) if growth else None,
        "inflation_z": (sum(infl) / len(infl)) if infl else None,
        "trail": trail,
    }
    regime["label"] = _regime_label(regime["growth_z"], regime["inflation_z"])
    if cc == "US":
        recessions = recession_periods_from_flag(read_series("fred:USREC"))
    else:
        gdp = mp.get("growth", {}).get("gdp_yoy")
        recessions = technical_recessions(read_series(gdp[0])) if gdp and gdp[1] == "yoy" else []
    nowcasts = []
    nc = mp.get("growth", {}).get("nowcast")
    if nc:
        t = await _tile("nowcast", nc[0], "level", cc)
        nowcasts.append(
            {
                "name": "GDPNow" if "GDPNOW" in nc[0] else nc[0],
                "series_id": nc[0],
                "value": t["last"],
                "ts": t["last_ts"],
            }
        )
    next_events = []
    try:
        from app.calendar.service import list_events

        today = date.today()
        evs = await list_events(today, today + timedelta(days=45), countries=[cc], min_importance=2)
        next_events = [
            {
                k: e[k]
                for k in (
                    "id",
                    "event_key",
                    "title",
                    "kind",
                    "importance",
                    "release_ts",
                    "release_ts_il",
                    "reference_period",
                    "status",
                )
            }
            | {
                "consensus": e["values"]["consensus"],
                "previous": e["values"]["previous"],
                "actual": e["values"]["actual"],
            }
            for e in evs[:20]
        ]
    except Exception as e:  # calendar not available yet
        log.debug("next_events unavailable: %s", e)
    esi = read_series(f"derived:ESI_{cc}")
    meta = (cat.get("countries") or {}).get(cc) or {}
    return {
        "country": cc,
        "name": meta.get("name", cc),
        "currency": meta.get("currency"),
        "sections": sections,
        "regime": regime,
        "recessions": recessions,
        "nowcasts": nowcasts,
        "next_events": next_events,
        "esi": {
            "last": float(esi["value"][-1]) if esi.height else None,
            "ts": esi["ts"][-1] if esi.height else None,
        },
        "asof": datetime.now(UTC),
    }


def _regime_label(g: float | None, i: float | None) -> str | None:
    if g is None or i is None:
        return None
    if g >= 0 and i >= 0:
        return "reflation"
    if g >= 0 and i < 0:
        return "goldilocks"
    if g < 0 and i >= 0:
        return "stagflation"
    return "deflationary slowdown"


async def compare(indicator: str, ccs: list[str]) -> dict:
    await ensure_catalog()
    out = {"indicator": indicator, "series": {}, "ts": [], "table": {}}
    wide: pl.DataFrame | None = None
    for cc in ccs:
        mp = await mapping_for(cc)
        entry = next((items[indicator] for items in mp.values() if indicator in items), None)
        if not entry:
            continue
        sid, kind = entry
        spec = await get_spec(sid)
        df = _scaled(read_series(sid), entry_overrides(cc, indicator).get("scale"))
        out["series"][cc.upper()] = {"series_id": sid, "transform": kind, "n": df.height}
        if df.is_empty():
            continue
        freq = spec.freq if spec else "1mo"
        x = transform(df.sort("ts"), kind, freq) if kind != "level" else df.sort("ts")
        m = (
            resample(x, "1mo", "last")
            if freq in ("1d", "1w")
            else x.with_columns(pl.col("ts").dt.truncate("1mo"))
        )
        m = m.unique(subset=["ts"], keep="last").select(["ts", pl.col("value").alias(cc.upper())])
        wide = m if wide is None else wide.join(m, on="ts", how="full", coalesce=True)
    if wide is not None:
        wide = wide.sort("ts").tail(120)
        out["ts"] = [t.isoformat()[:10] for t in wide["ts"].to_list()]
        out["table"] = {c: wide[c].to_list() for c in wide.columns if c != "ts"}
    return out


async def heatmap(ccs: list[str] | None = None, indicators: list[str] | None = None) -> dict:
    await ensure_catalog()
    cat = load_catalog()
    ccs = [c.upper() for c in (ccs or [c["cc"] for c in countries()])]
    inds = indicators or [k for keys in (cat.get("sections") or {}).values() for k in keys]
    cells = []
    for cc in ccs:
        mp = await mapping_for(cc)
        flat = {k: v for items in mp.values() for k, v in items.items()}
        for ind in inds:
            if ind not in flat:
                cells.append({"country": cc, "indicator": ind, "z": None, "last": None, "series_id": None})
                continue
            t = await _tile(ind, flat[ind][0], flat[ind][1], cc)
            cells.append(
                {
                    "country": cc,
                    "indicator": ind,
                    "z": t["zscore_3y"],
                    "last": t["last"],
                    "last_ts": t["last_ts"],
                    "series_id": t["series_id"],
                    "stale": t["stale"],
                }
            )
    return {
        "countries": ccs,
        "indicators": inds,
        "names": {k: (cat.get("indicators") or {}).get(k, {}).get("name", k) for k in inds},
        "cells": cells,
    }


async def refresh_country(cc: str, full: bool = False) -> list[dict]:
    await ensure_catalog()
    mp = await mapping_for(cc)
    ids = sorted({sid for items in mp.values() for sid, _ in items.values()})
    out = []
    for sid in ids:
        try:
            out.append(await refresh_series(sid, full=full))
        except Exception as e:
            out.append({"series_id": sid, "status": "error", "error": str(e)[:200]})
    return out


async def refresh_all() -> list[dict]:
    out = []
    for c in countries():
        out.extend(await refresh_country(c["cc"]))
    return out
