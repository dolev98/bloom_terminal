"""Parquet storage for observations: data/parquet/series/<encoded series_id>/<vintage>.parquet (append-only merge by ts)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, unquote

import polars as pl

from app.data.providers.base import OBS_SCHEMA, empty_observations


def encode_id(series_id: str) -> str:
    return quote(series_id, safe="")


def decode_id(name: str) -> str:
    return unquote(name)


class ParquetStore:
    def __init__(self, root: Path):
        self.root = root
        (self.root / "series").mkdir(parents=True, exist_ok=True)

    def path(self, series_id: str, vintage: str = "latest") -> Path:
        return self.root / "series" / encode_id(series_id) / f"{vintage}.parquet"

    def exists(self, series_id: str, vintage: str = "latest") -> bool:
        return self.path(series_id, vintage).exists()

    def read(
        self,
        series_id: str,
        start: date | datetime | None = None,
        end: date | datetime | None = None,
        vintage: str = "latest",
    ) -> pl.DataFrame:
        p = self.path(series_id, vintage)
        if not p.exists():
            return empty_observations()
        df = pl.read_parquet(p)
        if start is not None:
            df = df.filter(pl.col("ts") >= _to_dt(start))
        if end is not None:
            df = df.filter(pl.col("ts") <= _to_dt(end))
        return df

    def write(
        self, series_id: str, new: pl.DataFrame, vintage: str = "latest", replace: bool = False
    ) -> pl.DataFrame:
        """Merge `new` observations into the stored frame (new values win on equal ts). Returns the merged frame."""
        new = _normalize(new)
        p = self.path(series_id, vintage)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and not replace:
            old = pl.read_parquet(p)
            merged = (
                pl.concat([old, new], how="vertical_relaxed").unique(subset=["ts"], keep="last").sort("ts")
            )
        else:
            merged = new.sort("ts")
        tmp = p.with_suffix(".tmp.parquet")
        merged.write_parquet(tmp)
        tmp.replace(p)
        return merged

    def delete(self, series_id: str) -> None:
        d = self.root / "series" / encode_id(series_id)
        if d.exists():
            for f in d.iterdir():
                f.unlink()
            d.rmdir()

    def list_ids(self) -> list[str]:
        base = self.root / "series"
        return sorted(decode_id(d.name) for d in base.iterdir() if d.is_dir())

    def stats(self, series_id: str, vintage: str = "latest") -> dict:
        df = self.read(series_id, vintage=vintage)
        if df.is_empty():
            return {"n": 0, "first_ts": None, "last_ts": None}
        return {"n": df.height, "first_ts": df["ts"].min(), "last_ts": df["ts"].max()}


def _to_dt(d: date | datetime) -> datetime:
    if isinstance(d, datetime):
        return d
    return datetime(d.year, d.month, d.day)


def _normalize(df: pl.DataFrame) -> pl.DataFrame:
    if df.is_empty():
        return empty_observations()
    out = df.select(["ts", "value"])
    if out["ts"].dtype == pl.Date:
        out = out.with_columns(pl.col("ts").cast(pl.Datetime("us")))
    elif out["ts"].dtype != pl.Datetime("us"):
        out = out.with_columns(pl.col("ts").cast(pl.Datetime("us")))
    out = out.with_columns(pl.col("value").cast(pl.Float64))
    return out.drop_nulls(subset=["ts"]).select(list(OBS_SCHEMA.keys()))
