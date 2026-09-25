"""UK ONS time series (keyless JSON). Key: "CDID/DATASET" e.g. "D7G7/MM23" (CPI % y/y), "MGSX/LMS" (unemployment rate).

api.ons.gov.uk was retired in Nov 2024; the website serves the same JSON at
https://www.ons.gov.uk/<topic path>/timeseries/{cdid}/{dataset}/data. The topic path is dataset-specific
(DATASET_TOPICS lists candidates; a spec may pin it via params["path"]).
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
    ProviderError,
    RateSpec,
    SeriesSpec,
    empty_observations,
)
from app.data.retry import raise_for_retry, retrying

BASE = "https://www.ons.gov.uk"
DATASET_TOPICS: dict[str, tuple[str, ...]] = {
    "MM23": ("economy/inflationandpriceindices",),
    "PN2": ("economy/grossdomesticproductgdp",),
    "QNA": ("economy/grossdomesticproductgdp",),
    "DRSI": ("businessindustryandtrade/retailindustry",),
    "LMS": (
        "employmentandlabourmarket/peoplenotinwork/unemployment",
        "employmentandlabourmarket/peopleinwork/employmentandemployeetypes",
        "employmentandlabourmarket/peopleinwork/earningsandworkinghours",
    ),
    "DIOP": ("economy/economicoutputandproductivity/output",),
    "PUSF": ("economy/governmentpublicsectorandtaxes/publicsectorfinance",),
    "MRET": ("economy/nationalaccounts/balanceofpayments",),
}
_URL_RE = re.compile(r"ons\.gov\.uk/.*?/timeseries/([a-z0-9]+)/([a-z0-9]+)", re.I)
_MON = {
    m: i
    for i, m in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1
    )
}


def _parse_ons_date(s: str) -> date | None:
    parts = s.strip().upper().split()
    try:
        if len(parts) == 2 and parts[1] in _MON:
            return date(int(parts[0]), _MON[parts[1]], 1)
        if len(parts) == 2 and parts[1].startswith("Q"):
            return date(int(parts[0]), (int(parts[1][1]) - 1) * 3 + 1, 1)
        if len(parts) == 1:
            return date(int(parts[0]), 1, 1)
    except ValueError:
        return None
    return None


def parse_ons(data: dict) -> tuple[pl.DataFrame, str]:
    """Prefer monthly, then quarterly, then annual observations. Returns (frame, freq)."""
    for coll, freq in (("months", "1mo"), ("quarters", "1q"), ("years", "1y")):
        rows = (data or {}).get(coll) or []
        ts, vals = [], []
        for r in rows:
            d = _parse_ons_date(str(r.get("date", "")))
            v = r.get("value")
            if d is None or v in (None, ""):
                continue
            try:
                vals.append(float(v))
            except ValueError:
                continue
            ts.append(d)
        if ts:
            return pl.DataFrame({"ts": ts, "value": vals}).with_columns(
                pl.col("ts").cast(pl.Datetime("us"))
            ).sort("ts"), freq
    return empty_observations(), "1mo"


@dataclass
class OnsProvider(Provider):
    id: str = "ons"
    name: str = "UK Office for National Statistics"
    capabilities: Capability = Capability.SERIES
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_second=2, per_minute=30, concurrency=2))
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(
            grey=False, attribution="Source: ONS (Open Government Licence v3.0)"
        )
    )

    def parse_url(self, url: str) -> str | None:
        m = _URL_RE.search(url)
        return f"{m.group(1).upper()}/{m.group(2).upper()}" if m else None

    @staticmethod
    def split_key(key: str) -> tuple[str, str]:
        if "/" not in key:
            raise ProviderError(f"ONS key must be CDID/DATASET, got {key!r}")
        cdid, ds = key.split("/", 1)
        return cdid.lower(), ds.lower()

    async def _fetch(self, key: str, path: str | None = None) -> dict:
        cdid, ds = self.split_key(key)
        client = get_client()
        candidates = (
            (path,)
            if path
            else DATASET_TOPICS.get(ds.upper(), ()) + tuple(t for ts in DATASET_TOPICS.values() for t in ts)
        )
        last: int | None = None
        for topic in dict.fromkeys(candidates):
            url = f"{BASE}/{topic.strip('/')}/timeseries/{cdid}/{ds}/data"
            async for attempt in retrying():
                with attempt:
                    resp = await client.get(url, headers={"Accept": "application/json"})
                    if resp.status_code == 404:
                        break
                    raise_for_retry(resp)
            if resp.status_code == 200:
                return resp.json()
            last = resp.status_code
        raise ProviderError(f"ONS: no topic path served {key} (last status {last})")

    async def describe(self, key: str) -> SeriesSpec:
        data = await self._fetch(key)
        desc = data.get("description") or {}
        _, freq = parse_ons(data)
        unit = desc.get("unit") or ""
        return SeriesSpec(
            series_id=f"ons:{key.upper()}",
            provider="ons",
            provider_key=key.upper(),
            name=desc.get("title", key),
            freq=freq,
            unit=unit,
            value_kind="survey" if unit == "%" else "level_index",
            default_transform="level" if unit == "%" else "yoy",
            country="GB",
            category="macro",
        )

    async def get_series(
        self, spec: SeriesSpec, since: date | None = None, vintage: date | None = None
    ) -> pl.DataFrame:
        df, _ = parse_ons(await self._fetch(spec.provider_key, spec.params.get("path")))
        if since and not df.is_empty():
            df = df.filter(pl.col("ts") >= pl.lit(since).cast(pl.Datetime("us")))
        return df

    async def health(self) -> dict:
        try:
            df = await self.get_series(
                SeriesSpec(series_id="ons:D7G7/MM23", provider="ons", provider_key="D7G7/MM23")
            )
            return {"ok": df.height > 0}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
