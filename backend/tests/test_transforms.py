from datetime import datetime

import polars as pl

from app.analytics.transforms import align, resample, transform


def _df(vals):
    return pl.DataFrame(
        {"ts": [datetime(2024, 1, i + 1) for i in range(len(vals))], "value": vals}
    ).with_columns(pl.col("ts").cast(pl.Datetime("us")))


def test_log_ret_and_diff_bp():
    df = _df([100.0, 110.0, 99.0])
    lr = transform(df, "log_ret")
    assert lr.height == 2
    assert abs(lr["value"][0] - 0.0953) < 1e-3
    bp = transform(_df([4.0, 4.25]), "diff_bp")
    assert abs(bp["value"][0] - 25.0) < 1e-9


def test_yoy_uses_freq_periods():
    df = pl.DataFrame(
        {
            "ts": [datetime(2023, m, 1) for m in range(1, 13)] + [datetime(2024, 1, 1)],
            "value": [100.0] * 12 + [110.0],
        }
    ).with_columns(pl.col("ts").cast(pl.Datetime("us")))
    yoy = transform(df, "yoy", freq="1mo")
    assert yoy.height == 1
    assert abs(yoy["value"][0] - 10.0) < 1e-9


def test_resample_monthly_last():
    df = _df([1.0, 2.0, 3.0, 4.0, 5.0])
    out = resample(df, "1mo", how="last")
    assert out.height == 1
    assert out["value"][0] == 5.0


def test_align_inner_and_outer():
    a = _df([1.0, 2.0, 3.0])
    b = _df([10.0, 20.0])
    inner = align(a, b)
    assert inner.height == 2 and set(inner.columns) == {"ts", "a", "b"}
    outer = align(a, b, how="outer")
    assert outer.height == 3 and outer["b"][2] == 20.0
