"""Correlation service: resolve series (catalog or OHLCV close), analyze a pair, universe matrices, discovery
rankings, pair catalog CRUD, and the nightly precompute cache under settings.derived_dir/correlation."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import yaml
from sqlalchemy import delete, select

from app.analytics import align as al
from app.analytics import correlation as corr
from app.analytics import pca as pca_mod
from app.analytics import regimes as reg
from app.analytics import stationarity as stat
from app.analytics.transforms import transform as apply_transform
from app.core.config import get_settings
from app.correlation.models import Pair
from app.data.catalog.loader import get_spec, list_specs
from app.data.providers.base import DEFAULT_TRANSFORM_BY_KIND
from app.data.series_service import get_store, read_series
from app.data.store.sqlite import session_scope
from app.market.service import get_ohlcv_store, read_ohlcv

log = logging.getLogger(__name__)
SEED_PATH = Path(__file__).with_name("pairs_seed.yaml")
TRANSFORMS = ("level", "log_ret", "diff", "diff_bp", "pct", "yoy", "zscore")
DEFAULT_WINDOW = {"1d": 60, "1w": 26, "1mo": 24, "1q": 12, "1y": 5}
MAX_LAG = {"1d": 10, "1w": 8, "1mo": 12, "1q": 8, "1y": 3}
STABILITY_LOOKBACK_DAYS = 2 * 365
_TICKER_RE = re.compile(r"^[A-Za-z0-9^=.\-]+$")


class UnknownSeries(ValueError):
    pass


@dataclass
class SeriesInfo:
    id: str
    name: str
    freq: str
    value_kind: str
    default_transform: str
    unit: str
    publication_lag_days: int
    kind: str  # 'catalog' | 'ohlcv'
    country: str | None = None
    category: str | None = None


# --- series resolution ------------------------------------------------------------------------------
def ticker_from_id(series_id: str) -> str | None:
    """'AAPL' → 'AAPL'; 'yf:AAPL:close' → 'AAPL'; anything else (provider:key) → None."""
    sid = series_id.strip()
    if ":" not in sid:
        return sid.upper() if _TICKER_RE.match(sid) else None
    parts = sid.split(":")
    if len(parts) == 3 and parts[0] == "yf" and parts[2] in ("close", "adj_close"):
        return parts[1].upper()
    return None


async def resolve_info(series_id: str) -> SeriesInfo:
    spec = await get_spec(series_id)
    if spec is not None:
        return SeriesInfo(
            id=series_id,
            name=spec.name or series_id,
            freq=spec.freq,
            value_kind=spec.value_kind,
            default_transform=spec.default_transform
            or DEFAULT_TRANSFORM_BY_KIND.get(spec.value_kind, "level"),
            unit=spec.unit,
            publication_lag_days=spec.publication_lag_days,
            kind="catalog",
            country=spec.country,
            category=spec.category,
        )
    ticker = ticker_from_id(series_id)
    if ticker is None:
        raise UnknownSeries(f"unknown series {series_id!r} (not in catalog and not a ticker)")
    return SeriesInfo(
        id=ticker,
        name=ticker,
        freq="1d",
        value_kind="price",
        default_transform="log_ret",
        unit="price",
        publication_lag_days=0,
        kind="ohlcv",
        country="IL" if ticker.endswith(".TA") else "US",
        category="equity",
    )


def read_values(info: SeriesInfo, start: date | None = None, end: date | None = None) -> pl.DataFrame:
    if info.kind == "catalog":
        df = read_series(info.id, start, end)
    else:
        adj = info.id.endswith(":adj_close")
        df, _ = read_ohlcv(info.id, "1d", start, end, None, adjusted=adj)
        if df.is_empty():
            return pl.DataFrame(schema={"ts": pl.Datetime("us"), "value": pl.Float64})
        df = df.select(pl.col("ts"), pl.col("close").cast(pl.Float64).alias("value"))
    return df.drop_nulls("value").sort("ts")


async def load_series(
    series_id: str, start: date | None = None, end: date | None = None
) -> tuple[SeriesInfo, pl.DataFrame]:
    info = await resolve_info(series_id)
    return info, await asyncio.to_thread(read_values, info, start, end)


def pick_transform(info: SeriesInfo, override: str | None) -> str:
    if override and override != "default":
        if override not in TRANSFORMS:
            raise ValueError(f"unknown transform {override!r}")
        return override
    return info.default_transform if info.default_transform in TRANSFORMS else "level"


def _iso(ts: pl.Series) -> list[str]:
    return [t.isoformat() for t in ts.to_list()]


def _col(df: pl.DataFrame, c: str, d: int = 6) -> list[float | None]:
    return [None if v is None or not np.isfinite(v) else round(float(v), d) for v in df[c].to_list()]


def _transform_col(df: pl.DataFrame, col: str, kind: str, freq: str) -> pl.DataFrame:
    """Apply a transforms.transform kind to one column of an aligned frame (kept on the aligned grid)."""
    if kind == "level":
        return df.with_columns(pl.col(col).alias(f"{col}_t"))
    t = apply_transform(df.select(pl.col("ts"), pl.col(col).alias("value")), kind, freq)
    return df.join(t.rename({"value": f"{col}_t"}), on="ts", how="left")


# --- pair analysis --------------------------------------------------------------------------------------
def _analyze_pair_sync(
    info_a: SeriesInfo,
    info_b: SeriesInfo,
    raw_a: pl.DataFrame,
    raw_b: pl.DataFrame,
    transform_a: str,
    transform_b: str,
    freq: str,
    window: int | None,
    lag_b: int,
    methods: set[str],
    pub_lag: bool,
    invert_b: bool,
    regime: dict | None,
    regime_series: pl.DataFrame | None,
    max_lag: int | None,
    z_window: int | None,
    min_overlap: int,
) -> dict:
    warnings: list[str] = []
    target, warn = al.resolve_freq(freq, info_a.freq, info_b.freq)
    if warn:
        warnings.append(warn)
    if pub_lag:
        raw_a = al.apply_publication_lag(raw_a, info_a.publication_lag_days)
        raw_b = al.apply_publication_lag(raw_b, info_b.publication_lag_days)
    if raw_a.is_empty() or raw_b.is_empty():
        missing = [i.id for i, d in ((info_a, raw_a), (info_b, raw_b)) if d.is_empty()]
        raise al.AlignmentError(f"no stored observations for {missing}; refresh the series first")
    aligned = al.align_pair(
        raw_a, raw_b, freq=target, freq_a=info_a.freq, freq_b=info_b.freq, min_overlap=min_overlap
    )
    aligned = _transform_col(aligned, "a", transform_a, target)
    aligned = _transform_col(aligned, "b", transform_b, target)
    if invert_b:
        aligned = aligned.with_columns((-pl.col("b_t")).alias("b_t"))
    if lag_b:
        aligned = aligned.with_columns(pl.col("b_t").shift(int(lag_b)))
    stats_df = aligned.select(pl.col("ts"), pl.col("a_t").alias("a"), pl.col("b_t").alias("b")).drop_nulls()
    n = stats_df.height
    if n < max(10, min(min_overlap, 30)):
        raise al.AlignmentError(
            f"only {n} usable observations after transforms/lag (need ≥ {max(10, min(min_overlap, 30))})"
        )
    w = int(window) if window else DEFAULT_WINDOW.get(target, 60)
    w = max(5, min(w, n))
    max_lag = int(max_lag) if max_lag else MAX_LAG.get(target, 10)

    out: dict = {
        "a": {**asdict(info_a), "transform": transform_a, "n_raw": raw_a.height},
        "b": {**asdict(info_b), "transform": transform_b, "n_raw": raw_b.height, "inverted": invert_b},
        "freq": target,
        "window": w,
        "lag_b": int(lag_b),
        "pub_lag": pub_lag,
        "n": n,
        "start": stats_df["ts"][0].isoformat(),
        "end": stats_df["ts"][-1].isoformat(),
        "frame": {
            "ts": _iso(aligned["ts"]),
            "a": _col(aligned, "a"),
            "b": _col(aligned, "b"),
            "a_t": _col(aligned, "a_t"),
            "b_t": _col(aligned, "b_t"),
        },
        "warnings": warnings,
    }
    st = corr.pearson_spearman(stats_df["a"], stats_df["b"])
    out["stats"] = st
    if st["n_eff"] is not None and st["n"] and st["n_eff"] < 0.5 * st["n"]:
        warnings.append(
            f"inputs are autocorrelated: effective n ≈ {st['n_eff']:.0f} of {st['n']} — use the corrected CI/p"
        )

    if "rolling" in methods:
        rp = corr.rolling_corr(stats_df, w, method="pearson")
        rs = corr.rolling_corr(stats_df, w, method="spearman")
        ew = corr.ewma_corr(stats_df, halflife=max(2.0, w / 3.0))
        out["rolling"] = {
            "ts": _iso(rp["ts"]),
            "pearson": _col(rp, "corr", 4),
            "lo": _col(rp, "lo", 4),
            "hi": _col(rp, "hi", 4),
            "spearman": _col(rs, "corr", 4),
            "ewma": _col(ew, "corr", 4),
            "halflife": round(max(2.0, w / 3.0), 1),
        }
        vals = np.array([v for v in out["rolling"]["pearson"] if v is not None], dtype=float)
        if vals.size >= 3:
            out["stability"] = {
                "mean": round(float(vals.mean()), 4),
                "std": round(float(vals.std()), 4),
                "ratio": round(float(vals.mean() / vals.std()), 3) if vals.std() > 0 else None,
            }
    if "leadlag" in methods:
        out["lead_lag"] = corr.lead_lag(stats_df["a"], stats_df["b"], max_lag)
    if "beta" in methods:
        out["ols"] = corr.ols(stats_df["a"], stats_df["b"])
        rb = corr.rolling_beta(stats_df, w)
        out["rolling_beta"] = {
            "ts": _iso(rb["ts"]),
            "beta": _col(rb, "beta", 6),
            "alpha": _col(rb, "alpha", 6),
            "r2": _col(rb, "r2", 4),
        }
    if "stationarity" in methods:
        out["stationarity"] = {
            "a": stat.adf_kpss(stats_df["a"]),
            "b": stat.adf_kpss(stats_df["b"]),
            "a_level": stat.adf_kpss(aligned["a"]),
            "b_level": stat.adf_kpss(aligned["b"]),
        }
        gn = stat.granger_newbold_warning(aligned["a"], aligned["b"])
        out["granger_newbold"] = gn
        if transform_a == "level" and transform_b == "level" and gn.get("spurious"):
            warnings.append(gn["message"])
        for side in ("a", "b"):
            if out["stationarity"][side]["verdict"] == "I(1)-like":
                warnings.append(
                    f"{side.upper()} ({out[side]['transform']}) still looks I(1): correlation of levels is unreliable — use a difference/return transform or cointegration"
                )
    if "coint" in methods:
        out["cointegration"] = corr.cointegration(aligned["a"], aligned["b"], z_window=int(z_window or w))
        out["cointegration"]["ts"] = out["frame"]["ts"]
    if "granger" in methods:
        out["granger"] = corr.granger(stats_df["a"], stats_df["b"], maxlag=min(max_lag, 8))
    if regime is not None:
        rdf = None
        a_t = stats_df.select(pl.col("ts"), pl.col("a").alias("value"))
        if regime.get("kind") == "windows":
            rdf = reg.windows_regime(stats_df["ts"], list(regime.get("params", {}).get("windows", [])))
        else:
            rdf = reg.regime_from_preset(regime, regime_series, a_t)
        if rdf is None or rdf.is_empty():
            warnings.append(
                f"regime {regime.get('id') or regime.get('kind')} unavailable (no data or model failed)"
            )
        else:
            out["regimes"] = {
                "id": regime.get("id"),
                "name": regime.get("name"),
                "rows": reg.conditional_corr(stats_df, rdf),
            }
    out["warnings"] = warnings
    return out


async def analyze_pair(
    a: str,
    b: str,
    transform_a: str | None = None,
    transform_b: str | None = None,
    freq: str = "auto",
    window: int | None = None,
    lag_b: int = 0,
    start: date | None = None,
    end: date | None = None,
    methods: list[str] | None = None,
    pub_lag: bool = False,
    invert_b: bool = False,
    regime: str | dict | None = None,
    max_lag: int | None = None,
    z_window: int | None = None,
    min_overlap: int = 60,
) -> dict:
    """Full pair analysis: aligned frame (levels + transformed), rolling correlations with CI, lead/lag, OLS/beta,
    stationarity, optional cointegration/Granger/regimes. `methods` defaults to rolling, leadlag, beta, stationarity."""
    info_a, raw_a = await load_series(a, start, end)
    info_b, raw_b = await load_series(b, start, end)
    ta, tb = pick_transform(info_a, transform_a), pick_transform(info_b, transform_b)
    ms = set(methods or ["rolling", "leadlag", "beta", "stationarity"])
    regime_def: dict | None = None
    regime_series: pl.DataFrame | None = None
    if isinstance(regime, dict):
        regime_def = regime
    elif regime:
        regime_def = next((p for p in reg.PRESETS if p["id"] == regime), None)
        if regime_def is None:
            raise ValueError(f"unknown regime preset {regime!r}")
    if regime_def and regime_def.get("series_id"):
        try:
            _, regime_series = await load_series(regime_def["series_id"], start, end)
        except UnknownSeries:
            regime_series = None
    return await asyncio.to_thread(
        _analyze_pair_sync,
        info_a,
        info_b,
        raw_a,
        raw_b,
        ta,
        tb,
        freq,
        window,
        int(lag_b or 0),
        ms,
        pub_lag,
        invert_b,
        regime_def,
        regime_series,
        max_lag,
        z_window,
        min_overlap,
    )


# --- universes ------------------------------------------------------------------------------------------------
async def universe_ids(
    universe: str | list[str], category: str | None = None, country: str | None = None, tag: str | None = None
) -> list[str]:
    """'catalog' → enabled daily catalog series with stored data; 'watchlist' → watchlist tickers with OHLCV; list → as-is."""
    if isinstance(universe, list):
        return universe
    if universe == "watchlist":
        from app.api.routers.watchlists import all_tickers

        have = set(await asyncio.to_thread(get_ohlcv_store().tickers))
        return [t for t in await all_tickers() if t in have]
    specs = await list_specs(country=country, category=category, enabled_only=True)
    have = set(await asyncio.to_thread(get_store().list_ids))
    out = []
    for sp in specs:
        if sp.series_id not in have:
            continue
        if universe == "catalog" and sp.freq != "1d":
            continue
        if tag and tag not in sp.tags:
            continue
        out.append(sp.series_id)
    return out


async def _load_transformed(
    ids: list[str], start: date | None, freq: str, transform: str | None = None
) -> tuple[dict[str, pl.DataFrame], dict[str, SeriesInfo], dict[str, pl.DataFrame]]:
    """Load each id, put it on the `freq` grid (never upsampling) and apply its transform.
    Returns (transformed frames, infos, level frames on the same grid)."""
    frames: dict[str, pl.DataFrame] = {}
    levels: dict[str, pl.DataFrame] = {}
    infos: dict[str, SeriesInfo] = {}
    for sid in ids:
        try:
            info, raw = await load_series(sid, start, None)
        except UnknownSeries:
            continue
        if raw.is_empty():
            continue
        kind = pick_transform(info, transform)
        df = al.resample(raw, freq, "last") if al.freq_rank(freq) >= al.freq_rank(info.freq) else raw
        frames[sid] = apply_transform(df, kind, freq)
        levels[sid] = df
        infos[sid] = info
    return frames, infos, levels


async def matrix(
    series_ids: list[str],
    window_days: int = 730,
    freq: str = "1d",
    shrink: bool = False,
    method: str = "pearson",
    min_overlap: int = 60,
    transform: str | None = None,
) -> dict:
    """Clustered correlation matrix over a universe (each series in its default transform on a common grid)."""
    start = (datetime.now(UTC).date() - timedelta(days=int(window_days))) if window_days else None
    frames, infos, _ = await _load_transformed(series_ids, start, freq, transform)
    res = await asyncio.to_thread(pca_mod.analyze_universe, frames, min_overlap, shrink, method)
    res.update(
        {
            "window_days": window_days,
            "freq": freq,
            "shrink": shrink,
            "method": method,
            "computed_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
            "cached": False,
        }
    )
    res["names"] = {sid: infos[sid].name for sid in res["labels"] if sid in infos}
    res["transforms"] = {sid: pick_transform(infos[sid], transform) for sid in res["labels"] if sid in infos}
    res["missing"] = [sid for sid in series_ids if sid not in frames]
    return res


def _spark(vals: list[float | None], k: int = 40) -> list[float | None]:
    v = [x for x in vals if x is not None]
    if not v:
        return []
    if len(v) <= k:
        return [round(x, 3) for x in v]
    idx = np.linspace(0, len(v) - 1, k).round().astype(int)
    return [round(v[i], 3) for i in idx]


def _pair_row(
    x: str,
    y: str,
    fx: pl.DataFrame,
    fy: pl.DataFrame,
    freq: str,
    window: int,
    min_overlap: int,
    lx: pl.DataFrame | None = None,
    ly: pl.DataFrame | None = None,
) -> dict | None:
    """One discovery row: ρ/ρs/p/n, best lead-lag, 2y stability of the rolling corr, Engle–Granger p on levels."""
    try:
        df = al.align_pair(fx, fy, freq="auto", freq_a=freq, freq_b=freq, min_overlap=min_overlap)
    except al.AlignmentError:
        return None
    st = corr.pearson_spearman(df["a"], df["b"])
    if st["pearson"] is None:
        return None
    ll = corr.lead_lag(df["a"], df["b"], MAX_LAG.get(freq, 10))
    rc = corr.rolling_corr(df, window)
    cutoff = df["ts"][-1] - timedelta(days=STABILITY_LOOKBACK_DAYS)
    recent = rc.filter(pl.col("ts") >= cutoff)["corr"].drop_nulls()
    stability = None
    if recent.len() >= 3 and float(recent.std() or 0) > 0:
        stability = round(float(recent.mean()) / float(recent.std()), 3)
    coint_p = None
    if lx is not None and ly is not None:
        try:
            lv = al.align_pair(lx, ly, freq="auto", freq_a=freq, freq_b=freq, min_overlap=min_overlap)
            coint_p = corr.cointegration(lv["a"], lv["b"], z_window=window)["eg_p"]
        except Exception:
            coint_p = None
    return {
        "x": x,
        "y": y,
        "pearson": st["pearson"],
        "abs": abs(st["pearson"]),
        "spearman": st["spearman"],
        "p": st["pearson_p"],
        "n": st["n"],
        "n_eff": st["n_eff"],
        "best_lag": ll["best_lag"],
        "best_corr": ll["best_corr"],
        "stability": stability,
        "coint_p": coint_p,
        "spark": _spark(rc["corr"].to_list()),
    }


async def discover(
    x: str,
    universe: str | list[str] = "catalog",
    window_days: int = 730,
    freq: str = "1d",
    window: int | None = None,
    min_overlap: int = 60,
    limit: int = 50,
    category: str | None = None,
    country: str | None = None,
) -> dict:
    """Rank the universe by |ρ| with X (each side in its default transform)."""
    ids = await universe_ids(universe, category=category, country=country)
    ids = [i for i in ids if i != x]
    start = (datetime.now(UTC).date() - timedelta(days=int(window_days))) if window_days else None
    info_x, raw_x = await load_series(x, start, None)
    if raw_x.is_empty():
        raise al.AlignmentError(f"no stored observations for {x}")
    frames, infos, levels = await _load_transformed(ids, start, freq)
    lx = al.resample(raw_x, freq, "last") if al.freq_rank(freq) >= al.freq_rank(info_x.freq) else raw_x
    fx = apply_transform(lx, pick_transform(info_x, None), freq)
    w = int(window) if window else DEFAULT_WINDOW.get(freq, 60)

    def _run() -> list[dict]:
        rows = []
        for sid, fy in frames.items():
            r = _pair_row(x, sid, fx, fy, freq, w, min_overlap, lx, levels[sid])
            if r:
                r["name"] = infos[sid].name
                r["transform"] = pick_transform(infos[sid], None)
                r["freq"] = freq
                rows.append(r)
        rows.sort(key=lambda r: -r["abs"])
        return rows

    rows = await asyncio.to_thread(_run)
    return {
        "x": x,
        "x_name": info_x.name,
        "x_transform": pick_transform(info_x, None),
        "universe": universe if isinstance(universe, str) else "list",
        "window_days": window_days,
        "freq": freq,
        "window": w,
        "n_candidates": len(frames),
        "rows": rows[: int(limit)],
        "cached": False,
        "computed_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
    }


# --- precompute cache ------------------------------------------------------------------------------------------
def cache_dir() -> Path:
    p = get_settings().derived_dir / "correlation"
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_matrix_cache(universe: str, res: dict) -> Path:
    d = cache_dir()
    labels = res["labels"]
    wide = (
        pl.DataFrame(
            {"label": labels, **{lab: [row[j] for row in res["matrix"]] for j, lab in enumerate(labels)}}
        )
        if labels
        else pl.DataFrame({"label": []})
    )
    p = d / f"matrix_{universe}.parquet"
    tmp = p.with_suffix(".tmp.parquet")
    wide.write_parquet(tmp)
    tmp.replace(p)
    meta = {k: v for k, v in res.items() if k not in ("matrix",)}
    (d / f"matrix_{universe}.meta.json").write_text(json.dumps(meta, default=str), encoding="utf-8")
    return p


def read_matrix_cache(universe: str) -> dict | None:
    d = cache_dir()
    p, m = d / f"matrix_{universe}.parquet", d / f"matrix_{universe}.meta.json"
    if not p.exists() or not m.exists():
        return None
    meta = json.loads(m.read_text(encoding="utf-8"))
    wide = pl.read_parquet(p)
    labels = wide["label"].to_list()
    meta["matrix"] = [
        [(None if v is None else float(v)) for v in wide.row(i)[1:]] for i in range(len(labels))
    ]
    meta["cached"] = True
    return meta


def write_discover_cache(universe: str, rows: list[dict], window_days: int, freq: str, window: int) -> Path:
    d = cache_dir()
    p = d / f"discover_{universe}.parquet"
    if rows:
        df = pl.DataFrame(
            [
                {**{k: v for k, v in r.items() if k != "spark"}, "spark": json.dumps(r.get("spark", []))}
                for r in rows
            ]
        )
    else:
        df = pl.DataFrame({"x": [], "y": []})
    tmp = p.with_suffix(".tmp.parquet")
    df.write_parquet(tmp)
    tmp.replace(p)
    (d / f"discover_{universe}.meta.json").write_text(
        json.dumps(
            {
                "window_days": window_days,
                "freq": freq,
                "window": window,
                "computed_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "n_rows": len(rows),
            }
        ),
        encoding="utf-8",
    )
    return p


def read_discover_cache(universe: str, x: str, limit: int = 50) -> dict | None:
    d = cache_dir()
    p, m = d / f"discover_{universe}.parquet", d / f"discover_{universe}.meta.json"
    if not p.exists() or not m.exists():
        return None
    meta = json.loads(m.read_text(encoding="utf-8"))
    df = pl.read_parquet(p)
    if df.is_empty() or "x" not in df.columns:
        return None
    sub = df.filter(pl.col("x") == x).sort("abs", descending=True).head(limit)
    if sub.is_empty():
        return None
    rows = []
    for r in sub.to_dicts():
        r["spark"] = json.loads(r.get("spark") or "[]")
        rows.append(r)
    return {
        "x": x,
        "universe": universe,
        "rows": rows,
        "cached": True,
        "n_candidates": int(df.filter(pl.col("x") == x).height),
        **meta,
    }


async def precompute_universe(universe: str, window_days: int = 730, freq: str = "1d") -> dict:
    """Matrix + all-pairs discovery rows for one universe; writes the cache files. Idempotent."""
    ids = await universe_ids(universe)
    if len(ids) < 2:
        return {"universe": universe, "status": "skipped", "reason": f"{len(ids)} series with data"}
    res = await matrix(ids, window_days=window_days, freq=freq)
    write_matrix_cache(universe, res)
    start = datetime.now(UTC).date() - timedelta(days=int(window_days))
    frames, infos, levels = await _load_transformed(ids, start, freq)
    w = DEFAULT_WINDOW.get(freq, 60)
    keys = list(frames.keys())

    def _all_pairs() -> list[dict]:
        rows: list[dict] = []
        for i, x in enumerate(keys):
            for y in keys[i + 1 :]:
                r = _pair_row(x, y, frames[x], frames[y], freq, w, 60, levels[x], levels[y])
                if not r:
                    continue
                rows.append(
                    {**r, "name": infos[y].name, "transform": pick_transform(infos[y], None), "freq": freq}
                )
                rows.append(
                    {
                        **r,
                        "x": y,
                        "y": x,
                        "best_lag": (-r["best_lag"] if r["best_lag"] is not None else None),
                        "name": infos[x].name,
                        "transform": pick_transform(infos[x], None),
                        "freq": freq,
                    }
                )
        return rows

    rows = await asyncio.to_thread(_all_pairs)
    write_discover_cache(universe, rows, window_days, freq, w)
    return {
        "universe": universe,
        "status": "ok",
        "n_series": len(keys),
        "n_rows": len(rows),
        "pc1_share": res.get("pc1_share"),
    }


# --- pair catalog ---------------------------------------------------------------------------------------------
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:100]


async def ensure_seed_pairs(path: Path = SEED_PATH) -> int:
    """Insert seeded pairs that don't exist yet and delete retired seed pairs (idempotent).
    Returns the number inserted."""
    data = await asyncio.to_thread(lambda: yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    inserted = 0
    async with session_scope() as s:
        retired = list(data.get("retired") or [])
        if retired:
            await s.execute(delete(Pair).where(Pair.id.in_(retired), Pair.is_seed.is_(True)))
        existing = set((await s.execute(select(Pair.id))).scalars().all())
        for item in data.get("pairs", []):
            if item["id"] in existing:
                continue
            s.add(
                Pair(
                    id=item["id"],
                    name=item.get("name", item["id"]),
                    a=item["a"],
                    b=item["b"],
                    transform_a=item.get("transform_a"),
                    transform_b=item.get("transform_b"),
                    freq=str(item.get("freq", "auto")),
                    lag_b=int(item.get("lag_b", 0)),
                    rationale=item.get("rationale", ""),
                    tags=list(item.get("tags", [])),
                    country=item.get("country"),
                    is_seed=True,
                )
            )
            inserted += 1
    if inserted:
        log.info("correlation: seeded %d pairs", inserted)
    return inserted


async def list_pairs(country: str | None = None) -> list[dict]:
    async with session_scope() as s:
        stmt = select(Pair)
        if country:
            stmt = stmt.where(Pair.country == country)
        rows = (await s.execute(stmt.order_by(Pair.country, Pair.is_seed.desc(), Pair.name))).scalars().all()
        return [r.to_dict() for r in rows]


async def save_pair(data: dict) -> dict:
    pid = data.get("id") or _slug(f"{data.get('country') or 'user'}_{data['a']}_{data['b']}")
    async with session_scope() as s:
        row = await s.get(Pair, pid)
        vals = {
            "name": data.get("name") or f"{data['a']} vs {data['b']}",
            "a": data["a"],
            "b": data["b"],
            "transform_a": data.get("transform_a"),
            "transform_b": data.get("transform_b"),
            "freq": data.get("freq") or "auto",
            "lag_b": int(data.get("lag_b") or 0),
            "rationale": data.get("rationale") or "",
            "tags": list(data.get("tags") or []),
            "country": data.get("country"),
            "is_seed": False,
        }
        if row is None:
            row = Pair(id=pid, **vals)
            s.add(row)
        else:
            for k, v in vals.items():
                setattr(row, k, v)
        await s.flush()
        return row.to_dict()


async def delete_pair(pair_id: str) -> bool:
    async with session_scope() as s:
        row = await s.get(Pair, pair_id)
        if row is None:
            return False
        await s.delete(row)
        return True
