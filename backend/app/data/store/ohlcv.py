"""OHLCV lake: data/parquet/ohlcv/<encoded ticker>/<interval>.parquet (merge by ts)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import polars as pl

from app.data.providers.yfinance_provider import OHLCV_SCHEMA, empty_ohlcv
from app.data.store.parquet import encode_id


class OhlcvStore:
    def __init__(self, root: Path):
        self.root = root / "ohlcv"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, ticker: str, interval: str = "1d") -> Path:
        return self.root / encode_id(ticker.upper()) / f"{interval}.parquet"

    def read(self, ticker: str, interval: str = "1d", start: date | datetime | None = None, end: date | datetime | None = None) -> pl.DataFrame:
        p = self.path(ticker, interval)
        if not p.exists():
            return empty_ohlcv()
        df = pl.read_parquet(p)
        if start is not None:
            df = df.filter(pl.col("ts") >= _dt(start))
        if end is not None:
            df = df.filter(pl.col("ts") <= _dt(end))
        return df

    def write(self, ticker: str, new: pl.DataFrame, interval: str = "1d", replace: bool = False) -> pl.DataFrame:
        if new.is_empty():
            return self.read(ticker, interval)
        new = new.select(list(OHLCV_SCHEMA.keys())).with_columns(pl.col("ts").cast(pl.Datetime("us")))
        p = self.path(ticker, interval)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and not replace:
            old = pl.read_parquet(p)
            merged = pl.concat([old, new], how="vertical_relaxed").unique(subset=["ts"], keep="last").sort("ts")
        else:
            merged = new.sort("ts")
        tmp = p.with_suffix(".tmp.parquet")
        merged.write_parquet(tmp)
        tmp.replace(p)
        return merged

    def last_ts(self, ticker: str, interval: str = "1d") -> datetime | None:
        df = self.read(ticker, interval)
        return None if df.is_empty() else df["ts"].max()

    def tickers(self) -> list[str]:
        from app.data.store.parquet import decode_id

        return sorted(decode_id(d.name) for d in self.root.iterdir() if d.is_dir())


def _dt(d: date | datetime) -> datetime:
    return d if isinstance(d, datetime) else datetime(d.year, d.month, d.day)
