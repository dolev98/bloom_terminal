"""Alignment of two observation frames for cross-asset statistics. Pure polars, no I/O.

Rules: statistics are computed on the *coarsest* native grid of the two series (never upsample); trading
calendars are handled by intersecting observed dates; `lag_b` shifts B by k aligned periods (k>0 means B
is lagged, i.e. A_t is paired with B_{t-k}).
"""

from __future__ import annotations

import polars as pl

from app.analytics.transforms import resample as _resample

FREQ_RANK = {
    "tick": 0,
    "1m": 1,
    "5m": 2,
    "1h": 3,
    "1d": 4,
    "irregular": 4,
    "1w": 5,
    "1mo": 6,
    "1q": 7,
    "1y": 8,
}
STAT_FREQS = ("1d", "1w", "1mo", "1q", "1y")


class AlignmentError(ValueError):
    pass


def freq_rank(freq: str | None) -> int:
    return FREQ_RANK.get(freq or "1d", 4)


def lowest_freq(*freqs: str | None) -> str:
    """The coarsest of the given native frequencies — the only grid both series populate without upsampling."""
    best = "1d"
    for f in freqs:
        if freq_rank(f) > freq_rank(best):
            best = f or best
    return best if best in STAT_FREQS else "1d"


def resolve_freq(
    freq: str | None, freq_a: str | None, freq_b: str | None, allow_upsample: bool = False
) -> tuple[str, str | None]:
    """Target grid for statistics and an optional warning when the request had to be coarsened."""
    native = lowest_freq(freq_a, freq_b)
    if freq in (None, "", "auto"):
        return native, None
    if freq not in STAT_FREQS:
        return native, f"unsupported freq {freq!r}; using {native}"
    if freq_rank(freq) < freq_rank(native) and not allow_upsample:
        return native, f"requested {freq} would upsample {native} data; statistics use {native}"
    return freq, None


def apply_publication_lag(df: pl.DataFrame, lag_days: int) -> pl.DataFrame:
    """Shift timestamps forward so each value sits on the date it became *known* (as-of alignment)."""
    if df.is_empty() or not lag_days:
        return df
    return df.with_columns((pl.col("ts") + pl.duration(days=int(lag_days))).alias("ts"))


def resample(df: pl.DataFrame, freq: str, how: str = "last") -> pl.DataFrame:
    """Downsample to `freq` (label = start of the next period, as in transforms.resample). '1d' is a no-op."""
    if df.is_empty() or freq == "1d":
        return df
    return _resample(df.select(["ts", "value"]), freq, how)


def align_pair(
    a: pl.DataFrame,
    b: pl.DataFrame,
    freq: str = "auto",
    how_a: str = "last",
    how_b: str = "last",
    max_ffill: int | None = 5,
    min_overlap: int = 60,
    lag_b: int = 0,
    freq_a: str | None = "1d",
    freq_b: str | None = "1d",
    join: str = "intersect",
    allow_upsample: bool = False,
) -> pl.DataFrame:
    """Return `ts, a, b` on a common grid.

    freq: 'auto' (coarsest native), '1d', '1w', '1mo', '1q', '1y'. Requests finer than a side's native
    frequency are coarsened unless `allow_upsample` (chart overlay only).
    join: 'intersect' keeps dates observed on both sides (trading calendars); 'ffill' forward-fills gaps of up
    to `max_ffill` periods (None = unlimited) on an outer join.
    Raises AlignmentError when fewer than `min_overlap` rows remain.
    """
    target, _ = resolve_freq(freq, freq_a, freq_b, allow_upsample)
    aa = resample(a, target, how_a).select(["ts", "value"]).rename({"value": "a"})
    bb = resample(b, target, how_b).select(["ts", "value"]).rename({"value": "b"})
    if join == "intersect":
        out = aa.join(bb, on="ts", how="inner").sort("ts")
    else:
        out = aa.join(bb, on="ts", how="full", coalesce=True).sort("ts")
        out = out.with_columns(
            pl.col("a").fill_null(strategy="forward", limit=max_ffill),
            pl.col("b").fill_null(strategy="forward", limit=max_ffill),
        ).drop_nulls()
    if lag_b:
        out = out.with_columns(pl.col("b").shift(int(lag_b))).drop_nulls()
    if out.height < min_overlap:
        raise AlignmentError(f"only {out.height} overlapping observations at {target} (need {min_overlap})")
    return out


def shift_column(df: pl.DataFrame, col: str, k: int) -> pl.DataFrame:
    """Shift one column by k aligned rows and drop the rows that became null."""
    if not k:
        return df
    return df.with_columns(pl.col(col).shift(int(k))).drop_nulls(subset=[col])
