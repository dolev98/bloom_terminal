"""Read-only DuckDB over the Parquet lake for analytics (window functions, asof joins). Never writes."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from app.data.store.parquet import encode_id


class DuckReader:
    def __init__(self, parquet_root: Path):
        self.root = parquet_root

    def connect(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect(database=":memory:")
        con.execute("SET threads TO 4")
        return con

    def series_path(self, series_id: str, vintage: str = "latest") -> str:
        return str(self.root / "series" / encode_id(series_id) / f"{vintage}.parquet")

    def wide(self, series_ids: list[str], start: str | None = None, end: str | None = None) -> pl.DataFrame:
        """Return a wide frame ts × series (outer join on ts)."""
        con = self.connect()
        parts = []
        for i, sid in enumerate(series_ids):
            p = self.series_path(sid)
            if not Path(p).exists():
                continue
            parts.append(f"SELECT ts, value AS s{i} FROM read_parquet('{p}')")
        if not parts:
            return pl.DataFrame({"ts": []})
        sql = parts[0]
        for i, part in enumerate(parts[1:], start=1):
            sql = f"SELECT COALESCE(a.ts, b.ts) AS ts, {', '.join(f'a.s{j}' for j in range(i))}, b.s{i} FROM ({sql}) a FULL OUTER JOIN ({part}) b ON a.ts = b.ts"
        where = []
        if start:
            where.append(f"ts >= TIMESTAMP '{start}'")
        if end:
            where.append(f"ts <= TIMESTAMP '{end}'")
        if where:
            sql = f"SELECT * FROM ({sql}) WHERE {' AND '.join(where)}"
        df = con.execute(f"SELECT * FROM ({sql}) ORDER BY ts").pl()
        con.close()
        rename = {f"s{i}": sid for i, sid in enumerate(series_ids) if f"s{i}" in df.columns}
        return df.rename(rename)
