from datetime import UTC, datetime

import pandas as pd

from app.data.providers.yfinance_provider import normalize_history


def _frame(dates, closes, tz="America/New_York"):
    idx = pd.DatetimeIndex([pd.Timestamp(d, tz=tz) for d in dates])
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Adj Close": closes,
            "Volume": [1.0] * len(closes),
        },
        index=idx,
    )


def test_daily_bars_keyed_by_exchange_date_and_partial_session_dropped():
    pdf = _frame(["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"], [773.50, 773.38, 767.81, 770.0])
    # 10:00 New York on Sep 24: today's bar is still in progress -> dropped
    df = normalize_history(pdf, "SPY", "1d", now=datetime(2026, 9, 24, 14, 0, tzinfo=UTC))
    assert [d.day for d in df["ts"].to_list()] == [21, 22, 23]
    assert df["close"].to_list()[-1] == 767.81
    # after the close (16:30 New York) the bar is final and kept
    df2 = normalize_history(pdf, "SPY", "1d", now=datetime(2026, 9, 24, 20, 30, tzinfo=UTC))
    assert df2.height == 4


def test_round_the_clock_instruments_never_store_today():
    pdf = _frame(["2026-09-23", "2026-09-24"], [1.13, 1.14], tz="Europe/London")
    df = normalize_history(
        pdf, "EURUSD=X", "1d", now=datetime(2026, 9, 24, 20, 0, tzinfo=UTC)
    )  # 21:00 London, day not over
    assert df.height == 1


def test_tase_dates():
    pdf = _frame(["2026-09-23", "2026-09-24"], [4296.0, 4239.0], tz="Asia/Jerusalem")
    df = normalize_history(pdf, "TA35.TA", "1d", now=datetime(2026, 9, 24, 12, 0, tzinfo=UTC))  # 15:00 IL
    assert [d.day for d in df["ts"].to_list()] == [23]
