"""Technical indicators over OHLCV frames (polars, pure). Returned columns are added to the frame."""

from __future__ import annotations

import polars as pl


def sma(df: pl.DataFrame, n: int, col: str = "close") -> pl.DataFrame:
    return df.with_columns(pl.col(col).rolling_mean(n).alias(f"sma{n}"))


def ema(df: pl.DataFrame, n: int, col: str = "close") -> pl.DataFrame:
    return df.with_columns(pl.col(col).ewm_mean(span=n, adjust=False).alias(f"ema{n}"))


def rsi(df: pl.DataFrame, n: int = 14, col: str = "close") -> pl.DataFrame:
    delta = pl.col(col).diff()
    gain = pl.when(delta > 0).then(delta).otherwise(0.0)
    loss = pl.when(delta < 0).then(-delta).otherwise(0.0)
    avg_gain = gain.ewm_mean(alpha=1 / n, adjust=False)
    avg_loss = loss.ewm_mean(alpha=1 / n, adjust=False)
    rs = avg_gain / avg_loss
    return df.with_columns((100 - 100 / (1 + rs)).alias(f"rsi{n}"))


def macd(
    df: pl.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9, col: str = "close"
) -> pl.DataFrame:
    m = pl.col(col).ewm_mean(span=fast, adjust=False) - pl.col(col).ewm_mean(span=slow, adjust=False)
    out = df.with_columns(m.alias("macd"))
    out = out.with_columns(pl.col("macd").ewm_mean(span=signal, adjust=False).alias("macd_signal"))
    return out.with_columns((pl.col("macd") - pl.col("macd_signal")).alias("macd_hist"))


def bollinger(df: pl.DataFrame, n: int = 20, k: float = 2.0, col: str = "close") -> pl.DataFrame:
    mid = pl.col(col).rolling_mean(n)
    sd = pl.col(col).rolling_std(n)
    return df.with_columns(
        mid.alias("bb_mid"), (mid + k * sd).alias("bb_upper"), (mid - k * sd).alias("bb_lower")
    )


def atr(df: pl.DataFrame, n: int = 14) -> pl.DataFrame:
    prev_close = pl.col("close").shift(1)
    tr = pl.max_horizontal(
        pl.col("high") - pl.col("low"),
        (pl.col("high") - prev_close).abs(),
        (pl.col("low") - prev_close).abs(),
    )
    return df.with_columns(tr.ewm_mean(alpha=1 / n, adjust=False).alias(f"atr{n}"))


def vwap(df: pl.DataFrame) -> pl.DataFrame:
    tp = (pl.col("high") + pl.col("low") + pl.col("close")) / 3
    return df.with_columns(((tp * pl.col("volume")).cum_sum() / pl.col("volume").cum_sum()).alias("vwap"))


def apply(df: pl.DataFrame, names: list[str]) -> tuple[pl.DataFrame, list[str]]:
    """names like 'sma20', 'ema50', 'rsi14', 'macd', 'bb20', 'atr14', 'vwap'. Returns (frame, added columns)."""
    before = set(df.columns)
    for name in names:
        name = name.strip().lower()
        if not name:
            continue
        if name.startswith("sma"):
            df = sma(df, int(name[3:] or 20))
        elif name.startswith("ema"):
            df = ema(df, int(name[3:] or 20))
        elif name.startswith("rsi"):
            df = rsi(df, int(name[3:] or 14))
        elif name == "macd":
            df = macd(df)
        elif name.startswith("bb"):
            df = bollinger(df, int(name[2:] or 20))
        elif name.startswith("atr"):
            df = atr(df, int(name[3:] or 14))
        elif name == "vwap":
            df = vwap(df)
    added = [c for c in df.columns if c not in before]
    return df, added


def rebase(series: pl.Series, base: float = 100.0) -> pl.Series:
    first = series.drop_nulls()
    if first.is_empty():
        return series
    return series / first[0] * base
