"""Pure functions over observation frames (ts,value): transforms and resampling. No I/O."""

from __future__ import annotations

import polars as pl

PERIODS_PER_YEAR = {"1d": 252, "1w": 52, "1mo": 12, "1q": 4, "1y": 1}
RESAMPLE_EVERY = {"1d": "1d", "1w": "1w", "1mo": "1mo", "1q": "1q", "1y": "1y"}


def transform(df: pl.DataFrame, kind: str, freq: str = "1d", window: int = 20) -> pl.DataFrame:
    if df.is_empty() or kind == "level":
        return df
    v = pl.col("value")
    if kind == "log_ret":
        expr = (v / v.shift(1)).log()
    elif kind == "diff":
        expr = v - v.shift(1)
    elif kind == "diff_bp":
        expr = (v - v.shift(1)) * 100.0
    elif kind == "pct":
        expr = (v / v.shift(1) - 1.0) * 100.0
    elif kind == "yoy":
        n = PERIODS_PER_YEAR.get(freq, 12)
        expr = (v / v.shift(n) - 1.0) * 100.0
    elif kind == "zscore":
        expr = (v - v.rolling_mean(window)) / v.rolling_std(window)
    else:
        raise ValueError(f"unknown transform {kind!r}")
    return df.with_columns(expr.alias("value")).drop_nulls("value")


def resample(df: pl.DataFrame, freq: str, how: str = "last") -> pl.DataFrame:
    """Downsample to `freq` ('1w','1mo','1q','1y') using last/mean/sum/first."""
    if df.is_empty() or freq == "1d":
        return df
    every = RESAMPLE_EVERY.get(freq, freq)
    agg = {
        "last": pl.col("value").last(),
        "first": pl.col("value").first(),
        "mean": pl.col("value").mean(),
        "sum": pl.col("value").sum(),
    }[how]
    out = (
        df.sort("ts")
        .group_by_dynamic("ts", every=every, closed="left", label="right")
        .agg(agg.alias("value"))
    )
    return out.drop_nulls("value")


def align(a: pl.DataFrame, b: pl.DataFrame, how: str = "inner", ffill_limit: int = 5) -> pl.DataFrame:
    """Join two observation frames on ts. Returns ts, a, b. Forward-fills up to `ffill_limit` gaps for `how='outer'`."""
    aa = a.rename({"value": "a"})
    bb = b.rename({"value": "b"})
    if how == "inner":
        return aa.join(bb, on="ts", how="inner").sort("ts")
    out = aa.join(bb, on="ts", how="full", coalesce=True).sort("ts")
    if ffill_limit:
        out = out.with_columns(
            pl.col("a").fill_null(strategy="forward", limit=ffill_limit),
            pl.col("b").fill_null(strategy="forward", limit=ffill_limit),
        )
    return out.drop_nulls()
