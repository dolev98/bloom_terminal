"""Israel Central Bureau of Statistics price-index API (keyless; needs a browser-like User-Agent; Israel egress).

Key: the CBS index code, e.g. "120010" (CPI general). Optional field: ":pct" (m/m %), ":yoy" (y/y %); default = index level.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import polars as pl

from app.data.http import get_client
from app.data.providers.base import (
    Capability,
    LicenseSpec,
    Provider,
    RateSpec,
    SeriesSpec,
    empty_observations,
)
from app.data.retry import raise_for_retry, retrying

BASE = "https://api.cbs.gov.il/index/data/price"
UA = "Mozilla/5.0 (Macintosh) personal-research-terminal/0.1"
_URL_RE = re.compile(r"api\.cbs\.gov\.il/index/data/price\?.*id=(\d+)")


def parse_cbs_price(data: dict, fld: str | None = None) -> pl.DataFrame:
    months = (data or {}).get("month") or []
    ts, vals = [], []
    for block in months:
        for row in block.get("date") or []:
            y, m = row.get("year"), row.get("month")
            if not y or not m:
                continue
            if fld == "pct":
                v = row.get("percent")
            elif fld == "yoy":
                v = row.get("percentYear")
            else:
                v = (row.get("currBase") or {}).get("value")
            if v is None:
                continue
            ts.append(date(int(y), int(m), 1))
            vals.append(float(v))
    if not ts:
        return empty_observations()
    return (
        pl.DataFrame({"ts": ts, "value": vals})
        .with_columns(pl.col("ts").cast(pl.Datetime("us")))
        .unique(subset=["ts"], keep="last")
        .sort("ts")
    )


@dataclass
class CbsProvider(Provider):
    id: str = "cbs"
    name: str = "Israel CBS (price indices)"
    capabilities: Capability = Capability.SERIES
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_second=1, per_minute=20, concurrency=1))
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(
            grey=False,
            attribution="Source: Central Bureau of Statistics, Israel",
            note="CBS open data; requires User-Agent; Israel IP",
        )
    )
    egress: str = "il"

    def parse_url(self, url: str) -> str | None:
        m = _URL_RE.search(url)
        return m.group(1) if m else None

    async def describe(self, key: str) -> SeriesSpec:
        return SeriesSpec(
            series_id=f"cbs:{key}",
            provider="cbs",
            provider_key=key,
            name=f"CBS index {key}",
            freq="1mo",
            unit="index",
            value_kind="level_index",
            default_transform="yoy",
            country="IL",
            category="inflation",
        )

    async def get_series(
        self, spec: SeriesSpec, since: date | None = None, vintage: date | None = None
    ) -> pl.DataFrame:
        client = get_client()
        n = 1200
        if since:
            n = max(3, (date.today().year - since.year) * 12 + (date.today().month - since.month) + 2)
        params = {"id": spec.provider_key, "format": "json", "last": n}
        async for attempt in retrying():
            with attempt:
                resp = await client.get(
                    BASE, params=params, headers={"User-Agent": UA, "Accept": "application/json"}
                )
                raise_for_retry(resp)
        return parse_cbs_price(resp.json(), spec.field_name)

    async def health(self) -> dict:
        try:
            df = await self.get_series(
                SeriesSpec(series_id="cbs:120010", provider="cbs", provider_key="120010"),
                since=date.today().replace(day=1),
            )
            return {"ok": df.height > 0}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
