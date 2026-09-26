"""Manual / CSV series (ISM PMI, AAII, anything without an API). Values live in Parquet only; provider fetch is a no-op."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import polars as pl

from app.data.providers.base import (
    Capability,
    LicenseSpec,
    Provider,
    RateSpec,
    SeriesSpec,
    empty_observations,
)


@dataclass
class ManualProvider(Provider):
    id: str = "manual"
    name: str = "Manual / CSV entry"
    capabilities: Capability = Capability.SERIES
    rate: RateSpec = field(default_factory=lambda: RateSpec())
    license: LicenseSpec = field(default_factory=lambda: LicenseSpec(grey=False, note="user-entered"))

    async def describe(self, key: str) -> SeriesSpec:
        return SeriesSpec(
            series_id=f"manual:{key}",
            provider="manual",
            provider_key=key,
            name=key,
            freq="1mo",
            value_kind="survey",
            default_transform="diff",
            category="manual",
        )

    async def get_series(
        self, spec: SeriesSpec, since: date | None = None, vintage: date | None = None
    ) -> pl.DataFrame:
        return empty_observations()  # data is written directly via the catalog API


def parse_csv_observations(text: str) -> pl.DataFrame:
    """Accept 'date,value' CSV (header optional; comma, semicolon or tab). Dates ISO (YYYY-MM-DD or YYYY-MM)."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("date", "ts")):
            continue
        parts = [
            p.strip() for p in line.replace(";", ",").replace("\t", ",").split(",")
        ]  # tabs: pasted from Excel
        if len(parts) < 2:
            continue
        d = parts[0]
        if len(d) == 7:
            d = d + "-01"
        try:
            rows.append((date.fromisoformat(d), float(parts[1])))
        except ValueError:
            continue
    if not rows:
        return empty_observations()
    return (
        pl.DataFrame({"ts": [r[0] for r in rows], "value": [r[1] for r in rows]})
        .with_columns(pl.col("ts").cast(pl.Datetime("us")))
        .sort("ts")
    )
