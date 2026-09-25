"""Regime labelling (rule-based bands, trend of a policy rate, realized-vol quantiles, user date windows, optional
Markov switching) and conditional correlation per regime. Pure polars/numpy; statsmodels only for Markov."""

from __future__ import annotations

import warnings
from datetime import date, datetime

import numpy as np
import polars as pl

from app.analytics.correlation import fisher_ci, pearson_spearman

REGIME_SCHEMA = {"ts": pl.Datetime("us"), "regime": pl.String}

PRESETS: list[dict] = [
    {
        "id": "vix",
        "name": "VIX bands (<15 calm / 15–25 normal / >25 stress)",
        "series_id": "fred:VIXCLS",
        "kind": "bands",
        "params": {"edges": [15, 25], "labels": ["calm", "normal", "stress"]},
    },
    {
        "id": "curve",
        "name": "Curve inverted (T10Y3M < 0)",
        "series_id": "fred:T10Y3M",
        "kind": "bands",
        "params": {"edges": [0], "labels": ["inverted", "normal"]},
    },
    {
        "id": "fed",
        "name": "Fed cutting / hold / hiking (DFF 3-month change)",
        "series_id": "fred:DFF",
        "kind": "trend",
        "params": {"periods": 63, "threshold": 0.10, "labels": ["cutting", "hold", "hiking"]},
    },
    {
        "id": "hy",
        "name": "HY OAS (<3.5 tight / 3.5–5 mid / >5 wide)",
        "series_id": "fred:BAMLH0A0HYM2",
        "kind": "bands",
        "params": {"edges": [3.5, 5.0], "labels": ["tight", "mid", "wide"]},
    },
    {
        "id": "usrec",
        "name": "NBER recession (USREC)",
        "series_id": "fred:USREC",
        "kind": "bands",
        "params": {"edges": [0.5], "labels": ["expansion", "recession"]},
    },
    {
        "id": "boi_fed",
        "name": "BoI − Fed rate differential (<0 / >0)",
        "series_id": "derived:BOI_FED_DIFF",
        "kind": "bands",
        "params": {"edges": [0], "labels": ["boi_below_fed", "boi_above_fed"]},
    },
    {
        "id": "realized_vol",
        "name": "Realized-vol terciles of A (20-period)",
        "series_id": None,
        "kind": "vol_quantiles",
        "params": {"window": 20, "quantiles": [0.33, 0.67], "labels": ["low_vol", "mid_vol", "high_vol"]},
    },
    {
        "id": "markov",
        "name": "Markov switching (2 regimes on A, statsmodels)",
        "series_id": None,
        "kind": "markov",
        "params": {"k": 2},
    },
]


def _empty() -> pl.DataFrame:
    return pl.DataFrame(schema=REGIME_SCHEMA)


def bands_regime(df: pl.DataFrame, edges: list[float], labels: list[str]) -> pl.DataFrame:
    """Label each observation by the band its value falls in (edges ascending; len(labels) == len(edges)+1)."""
    if df.is_empty():
        return _empty()
    if len(labels) != len(edges) + 1:
        raise ValueError("labels must have len(edges)+1 entries")
    v = pl.col("value")
    expr = pl.when(v < edges[0]).then(pl.lit(labels[0]))
    for i in range(1, len(edges)):
        expr = expr.when(v < edges[i]).then(pl.lit(labels[i]))
    expr = expr.otherwise(pl.lit(labels[-1]))
    return df.select(pl.col("ts"), expr.alias("regime")).drop_nulls()


def trend_regime(
    df: pl.DataFrame,
    periods: int = 63,
    threshold: float = 0.1,
    labels: tuple[str, str, str] = ("cutting", "hold", "hiking"),
) -> pl.DataFrame:
    """Regime from the change over `periods` observations: < −threshold → labels[0], > threshold → labels[2]."""
    if df.is_empty():
        return _empty()
    d = pl.col("value") - pl.col("value").shift(int(periods))
    expr = (
        pl.when(d < -threshold)
        .then(pl.lit(labels[0]))
        .when(d > threshold)
        .then(pl.lit(labels[2]))
        .otherwise(pl.lit(labels[1]))
    )
    return df.sort("ts").select(pl.col("ts"), expr.alias("regime")).drop_nulls()


def vol_quantile_regime(
    returns: pl.DataFrame,
    window: int = 20,
    quantiles: tuple[float, float] = (0.33, 0.67),
    labels: tuple[str, ...] = ("low_vol", "mid_vol", "high_vol"),
) -> pl.DataFrame:
    """Realized-vol regime: rolling std of `value` (a returns/diff series) cut at full-sample quantiles."""
    if returns.is_empty():
        return _empty()
    rv = (
        returns.sort("ts")
        .with_columns(pl.col("value").rolling_std(int(window), min_samples=max(3, window // 2)).alias("rv"))
        .drop_nulls("rv")
    )
    if rv.is_empty():
        return _empty()
    edges = [float(rv["rv"].quantile(q)) for q in quantiles]
    return bands_regime(rv.select(pl.col("ts"), pl.col("rv").alias("value")), edges, list(labels))


def windows_regime(ts: pl.Series, windows: list[dict], other: str | None = None) -> pl.DataFrame:
    """User-defined windows [{start, end, label}] → regime per timestamp (outside all windows → `other`/null)."""
    out = pl.DataFrame({"ts": ts}).with_columns(pl.lit(other, dtype=pl.String).alias("regime"))
    for w in windows:
        s, e = _to_dt(w.get("start")), _to_dt(w.get("end"))
        cond = pl.lit(True)
        if s is not None:
            cond = cond & (pl.col("ts") >= s)
        if e is not None:
            cond = cond & (pl.col("ts") <= e)
        out = out.with_columns(
            pl.when(cond)
            .then(pl.lit(str(w.get("label", "window"))))
            .otherwise(pl.col("regime"))
            .alias("regime")
        )
    return out.drop_nulls("regime")


def _to_dt(v) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    return datetime.fromisoformat(str(v)[:19])


def markov_regime(df: pl.DataFrame, k: int = 2) -> pl.DataFrame | None:
    """Two-state MarkovRegression (switching mean+variance) on `value`; regimes named by ascending variance.
    Returns None when statsmodels fails to converge or the sample is too small."""
    if df.is_empty() or df.height < 100:
        return None
    try:
        from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

        y = df.sort("ts")["value"].to_numpy().astype(float)
        y = (y - y.mean()) / (y.std() or 1.0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = MarkovRegression(y, k_regimes=int(k), trend="c", switching_variance=True).fit(
                disp=False, maxiter=200
            )
        probs = np.asarray(res.smoothed_marginal_probabilities)
        if probs.ndim == 1:
            probs = probs.reshape(-1, 1)
        state = probs.argmax(axis=1)
        sig = [float(np.std(y[state == i])) if np.any(state == i) else 0.0 for i in range(int(k))]
        rank = {i: r for r, i in enumerate(sorted(range(int(k)), key=lambda i: sig[i]))}
        names = ["low_vol", "high_vol"] if k == 2 else [f"regime_{r + 1}" for r in range(int(k))]
        lab = [names[rank[int(s)]] for s in state]
        return df.sort("ts").select("ts").with_columns(pl.Series("regime", lab))
    except Exception:
        return None


def regime_from_preset(
    preset: dict, series: pl.DataFrame | None, a_transformed: pl.DataFrame | None = None
) -> pl.DataFrame | None:
    """Build a regime frame for a preset: bands/trend need the preset's `series`; vol/markov use A's transformed values."""
    kind, p = preset.get("kind"), preset.get("params", {})
    if kind == "bands" and series is not None:
        return bands_regime(series, list(p["edges"]), list(p["labels"]))
    if kind == "trend" and series is not None:
        return trend_regime(
            series,
            int(p.get("periods", 63)),
            float(p.get("threshold", 0.1)),
            tuple(p.get("labels", ("cutting", "hold", "hiking"))),
        )
    if kind == "vol_quantiles" and a_transformed is not None:
        return vol_quantile_regime(
            a_transformed,
            int(p.get("window", 20)),
            tuple(p.get("quantiles", (0.33, 0.67))),
            tuple(p.get("labels", ("low_vol", "mid_vol", "high_vol"))),
        )
    if kind == "markov" and a_transformed is not None:
        return markov_regime(a_transformed, int(p.get("k", 2)))
    return None


def conditional_corr(
    aligned: pl.DataFrame, regime: pl.DataFrame, min_n: int = 20, a: str = "a", b: str = "b"
) -> list[dict]:
    """Correlation of (a,b) within each regime. The regime label in force at each ts is the latest one at or
    before it (as-of join). Returns rows sorted by n desc."""
    if aligned.is_empty() or regime is None or regime.is_empty():
        return []
    reg = regime.select(["ts", "regime"]).sort("ts").with_columns(pl.col("ts").cast(pl.Datetime("us")))
    df = (
        aligned.sort("ts")
        .with_columns(pl.col("ts").cast(pl.Datetime("us")))
        .join_asof(reg, on="ts", strategy="backward")
        .drop_nulls("regime")
    )
    rows = []
    for (label,), grp in df.group_by(["regime"]):
        n = grp.height
        if n < min_n:
            rows.append({"regime": label, "n": n, "pearson": None, "spearman": None, "ci": [None, None]})
            continue
        st = pearson_spearman(grp[a], grp[b])
        lo, hi = fisher_ci(st["pearson"], st["n_eff"] or n)
        rows.append(
            {
                "regime": label,
                "n": n,
                "pearson": st["pearson"],
                "spearman": st["spearman"],
                "ci": [None if lo is None else round(lo, 4), None if hi is None else round(hi, 4)],
                "n_eff": st["n_eff"],
            }
        )
    rows.sort(key=lambda r: -r["n"])
    return rows
