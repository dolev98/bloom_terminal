from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

from app.analytics import align
from tests.correlation.conftest import days, obs


def test_resolve_freq_auto_is_coarsest_and_never_upsamples():
    assert align.resolve_freq("auto", "1d", "1mo") == ("1mo", None)
    assert align.resolve_freq("auto", "1w", "1d") == ("1w", None)
    target, warn = align.resolve_freq("1d", "1d", "1mo")
    assert target == "1mo" and warn and "upsample" in warn
    assert align.resolve_freq("1mo", "1d", "1d") == ("1mo", None)
    assert align.resolve_freq("1d", "1d", "1mo", allow_upsample=True) == ("1d", None)


def test_align_intersects_trading_calendars(rng):
    # A observed Mon–Fri, B observed Sun–Thu → common grid is Mon–Thu
    start = datetime(2024, 1, 1)
    all_days = [start + timedelta(days=i) for i in range(200)]
    a_ts = pl.Series([d for d in all_days if d.weekday() < 5], dtype=pl.Datetime("us"))
    b_ts = pl.Series([d for d in all_days if d.weekday() in (6, 0, 1, 2, 3)], dtype=pl.Datetime("us"))
    a = obs(a_ts, rng.standard_normal(a_ts.len()))
    b = obs(b_ts, rng.standard_normal(b_ts.len()))
    out = align.align_pair(a, b, freq="auto", min_overlap=10)
    wd = {d.weekday() for d in out["ts"].to_list()}
    assert wd == {0, 1, 2, 3}
    assert set(out.columns) == {"ts", "a", "b"}


def test_align_lag_b_pairs_a_t_with_b_t_minus_k(rng):
    ts = days(100)
    x = np.arange(100, dtype=float)
    a = obs(ts, x)
    b = obs(ts, x + 1000)
    out = align.align_pair(a, b, lag_b=2, min_overlap=10)
    # row for A_t carries B_{t-2}: b - a == 1000 - 2
    assert out.height == 98
    assert np.allclose((out["b"] - out["a"]).to_numpy(), 998.0)
    out0 = align.align_pair(a, b, lag_b=0, min_overlap=10)
    assert out0.height == 100


def test_align_daily_vs_monthly_uses_monthly_grid(rng):
    daily = obs(days(400, business=True), rng.standard_normal(400).cumsum())
    monthly_ts = pl.Series(
        [datetime(2022, m, 1) for m in range(1, 13)] + [datetime(2023, m, 1) for m in range(1, 7)],
        dtype=pl.Datetime("us"),
    )
    monthly = obs(monthly_ts, rng.standard_normal(18))
    out = align.align_pair(daily, monthly, freq="auto", freq_a="1d", freq_b="1mo", min_overlap=5)
    assert 12 <= out.height <= 18
    # every label is a first-of-month (period label = start of next month)
    assert all(t.day == 1 for t in out["ts"].to_list())
    # requesting daily is silently coarsened (statistics never upsample)
    out2 = align.align_pair(daily, monthly, freq="1d", freq_a="1d", freq_b="1mo", min_overlap=5)
    assert out2.height == out.height


def test_apply_publication_lag_shifts_timestamps():
    df = obs(days(3), [1.0, 2.0, 3.0])
    shifted = align.apply_publication_lag(df, 25)
    assert shifted["ts"][0] == df["ts"][0] + timedelta(days=25)
    assert align.apply_publication_lag(df, 0).equals(df)


def test_min_overlap_raises(rng):
    a = obs(days(30), rng.standard_normal(30))
    b = obs(days(30, start=datetime(2022, 1, 20)), rng.standard_normal(30))
    with pytest.raises(align.AlignmentError):
        align.align_pair(a, b, min_overlap=60)


def test_ffill_join_fills_short_gaps(rng):
    ts = days(50)
    a = obs(ts, rng.standard_normal(50))
    keep = [i for i in range(50) if i % 7 != 3]  # drop every 7th observation of B
    b = obs(ts[keep], rng.standard_normal(len(keep)))
    inter = align.align_pair(a, b, join="intersect", min_overlap=10)
    filled = align.align_pair(a, b, join="ffill", max_ffill=2, min_overlap=10)
    assert inter.height == len(keep)
    assert filled.height == 50
