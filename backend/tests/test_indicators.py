from datetime import datetime, timedelta

import polars as pl

from app.analytics import indicators as ind


def _ohlcv(n=60):
    base = datetime(2024, 1, 1)
    close = [100 + i + (5 if i % 7 == 0 else 0) for i in range(n)]
    return pl.DataFrame(
        {
            "ts": [base + timedelta(days=i) for i in range(n)],
            "open": close,
            "high": [c + 1 for c in close],
            "low": [c - 1 for c in close],
            "close": close,
            "adj_close": close,
            "volume": [1000.0] * n,
        }
    )


def test_apply_adds_expected_columns():
    df, added = ind.apply(_ohlcv(), ["sma20", "ema10", "rsi14", "macd", "bb20", "atr14", "vwap"])
    assert set(added) == {
        "sma20",
        "ema10",
        "rsi14",
        "macd",
        "macd_signal",
        "macd_hist",
        "bb_mid",
        "bb_upper",
        "bb_lower",
        "atr14",
        "vwap",
    }
    assert df["sma20"][19] is not None and df["sma20"][18] is None
    assert 0 <= df["rsi14"][-1] <= 100
    assert df["bb_upper"][-1] > df["bb_mid"][-1] > df["bb_lower"][-1]


def test_rebase():
    s = pl.Series([None, 50.0, 100.0])
    out = ind.rebase(s)
    assert out.to_list()[1:] == [100.0, 200.0]
