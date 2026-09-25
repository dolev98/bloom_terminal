from datetime import datetime

import polars as pl

from app.data.store.ohlcv import OhlcvStore


def test_ohlcv_merge(tmp_path):
    st = OhlcvStore(tmp_path)
    df = pl.DataFrame(
        {
            "ts": [datetime(2024, 1, 2), datetime(2024, 1, 3)],
            "open": [1.0, 2.0],
            "high": [1.5, 2.5],
            "low": [0.5, 1.5],
            "close": [1.2, 2.2],
            "adj_close": [1.2, 2.2],
            "volume": [10.0, 20.0],
        }
    )
    st.write("aapl", df)
    df2 = df.with_columns(pl.col("close") + 1).tail(1)
    merged = st.write("AAPL", df2)
    assert merged.height == 2 and merged["close"][-1] == 3.2
    assert st.tickers() == ["AAPL"] and st.last_ts("AAPL") == datetime(2024, 1, 3)
