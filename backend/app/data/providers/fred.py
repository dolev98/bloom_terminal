"""FRED / ALFRED adapter. Free key; 120 req/min; up to 100k obs per call; server-side transforms and vintages.

ToS: the UI must show "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis."
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

BASE = "https://api.stlouisfed.org/fred"
FRED_ATTRIBUTION = "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis."

_FREQ_MAP = {
    "Daily": "1d",
    "Weekly": "1w",
    "Biweekly": "1w",
    "Monthly": "1mo",
    "Quarterly": "1q",
    "Annual": "1y",
}
_URL_RE = re.compile(r"fred\.stlouisfed\.org/(?:series|graph)/?\??(?:id=)?([A-Za-z0-9_]+)")


@dataclass
class FredProvider(Provider):
    id: str = "fred"
    name: str = "FRED / ALFRED (St. Louis Fed)"
    capabilities: Capability = Capability.SERIES | Capability.SEARCH
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_minute=110, concurrency=4))
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(grey=False, attribution=FRED_ATTRIBUTION)
    )
    requires: tuple[str, ...] = ("fred_api_key",)
    api_key: str = ""

    def parse_url(self, url: str) -> str | None:
        m = _URL_RE.search(url)
        return m.group(1) if m else None

    async def _get(self, path: str, **params) -> dict:
        client = get_client()
        params = {"api_key": self.api_key, "file_type": "json", **params}
        async for attempt in retrying():
            with attempt:
                resp = await client.get(f"{BASE}/{path}", params=params)
                raise_for_retry(resp)
        data = resp.json()
        if "error_message" in data:
            raise ProviderError(f"FRED: {data['error_message']}")
        return data

    async def describe(self, key: str) -> SeriesSpec:
        data = await self._get("series", series_id=key)
        s = (data.get("seriess") or [{}])[0]
        units = s.get("units_short") or s.get("units") or ""
        kind = _guess_kind(units, s.get("title", ""))
        return SeriesSpec(
            series_id=f"fred:{key}",
            provider="fred",
            provider_key=key,
            name=s.get("title", key),
            description=s.get("notes", "")[:2000],
            freq=_FREQ_MAP.get(s.get("frequency", "Daily").split(",")[0], "1d"),
            unit=_norm_unit(units),
            sa=(s.get("seasonal_adjustment_short") == "SA"),
            value_kind=kind,
            default_transform={
                "yield": "diff_bp",
                "spread": "diff_bp",
                "price": "log_ret",
                "level_index": "log_ret",
                "stock": "yoy",
                "flow": "pct",
                "survey": "diff",
            }.get(kind, "level"),
            supports_vintage=True,
            vintage_policy="native",
            country=_guess_country(s.get("title", "")),
            category=_guess_category(s.get("title", ""), units),
            license_note="FRED; check series-level copyright (e.g. ICE BofA, Cboe) for personal-use limits",
        )

    async def search(self, q: str) -> list[SeriesSpec]:
        data = await self._get(
            "series/search", search_text=q, limit=20, order_by="popularity", sort_order="desc"
        )
        out = []
        for s in data.get("seriess", []):
            out.append(
                SeriesSpec(
                    series_id=f"fred:{s['id']}",
                    provider="fred",
                    provider_key=s["id"],
                    name=s.get("title", s["id"]),
                    freq=_FREQ_MAP.get(s.get("frequency", "Daily").split(",")[0], "1d"),
                    unit=s.get("units_short", ""),
                    supports_vintage=True,
                    vintage_policy="native",
                )
            )
        return out

    async def get_series(
        self, spec: SeriesSpec, since: date | None = None, vintage: date | None = None
    ) -> pl.DataFrame:
        params: dict = {"series_id": spec.provider_key, "limit": 100000}
        if since:
            params["observation_start"] = since.isoformat()
        if vintage:
            params["realtime_start"] = vintage.isoformat()
            params["realtime_end"] = vintage.isoformat()
        for k in ("units", "frequency", "aggregation_method"):
            if spec.params.get(k):
                params[k] = spec.params[k]
        data = await self._get("series/observations", **params)
        obs = data.get("observations", [])
        if not obs:
            return empty_observations()
        df = pl.DataFrame({"ts": [o["date"] for o in obs], "value": [o["value"] for o in obs]})
        df = df.with_columns(
            pl.col("ts").str.strptime(pl.Datetime("us"), "%Y-%m-%d"),
            pl.when(pl.col("value") == ".")
            .then(None)
            .otherwise(pl.col("value"))
            .cast(pl.Float64, strict=False)
            .alias("value"),
        )
        return df.drop_nulls("value")

    async def vintage_dates(self, key: str) -> list[str]:
        data = await self._get("series/vintagedates", series_id=key, limit=10000)
        return data.get("vintage_dates", [])

    async def release_dates(self, start: date, end: date) -> list[dict]:
        data = await self._get(
            "releases/dates",
            realtime_start=start.isoformat(),
            realtime_end=end.isoformat(),
            include_release_dates_with_no_data="true",
            limit=1000,
        )
        return data.get("release_dates", [])

    async def health(self) -> dict:
        try:
            await self._get("series", series_id="DGS10")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}


_LABOUR = ("unemployment", "payroll", "claims", "labor", "employment", "earnings", "jobs")
_INFLATION = ("cpi", "consumer price", "price index", "inflation", "pce")


def _is_percent(units: str) -> bool:
    u = units.lower().strip()
    return "percent" in u or u.startswith("%")


def _norm_unit(units: str) -> str:
    """FRED short units -> catalog unit codes (pct, usd_bn, usd_mn, index, thousands); unknown kept as-is."""
    u = units.lower().strip()
    if _is_percent(units):
        return "pct"
    if u.startswith("bil") and "$" in u or u.startswith("billions of dollars"):
        return "usd_bn"
    if u.startswith("mil") and "$" in u or u.startswith("millions of dollars"):
        return "usd_mn"
    if u.startswith("thous"):
        return "thousands"
    if u.startswith("index"):
        return "index"
    return units


def _guess_kind(units: str, title: str) -> str:
    u = units.lower()
    t = title.lower()
    pct = _is_percent(units)
    if pct and any(w in t for w in _LABOUR):
        return "survey"
    if pct and ("yield" in t or "rate" in t or "treasury" in t):
        return "yield"
    if "spread" in t or "oas" in t:
        return "spread"
    if "index" in u or "index" in t:
        return "level_index"
    if u.startswith("bil") or u.startswith("mil") or "dollars" in u or "$" in u:
        return (
            "stock"
            if any(w in t for w in ("assets", "m2", "m1", "balance", "reserve", "deposits", "debt"))
            else "flow"
        )
    if pct:
        return "survey"
    return "other"


def _guess_country(title: str) -> str | None:
    t = title.lower()
    for name, code in (
        ("israel", "IL"),
        ("euro area", "EA"),
        ("germany", "DE"),
        ("united kingdom", "GB"),
        ("japan", "JP"),
        ("china", "CN"),
        ("canada", "CA"),
    ):
        if name in t:
            return code
    return "US"


def _guess_category(title: str, units: str) -> str:
    t = title.lower()
    # labour and inflation first: "Unemployment Rate" / "Inflation Rate" are not interest rates
    if any(w in t for w in _LABOUR):
        return "labour"
    if any(w in t for w in _INFLATION) and "breakeven" not in t:
        return "inflation"
    if any(w in t for w in ("treasury", "yield", "federal funds", "sofr", "rate", "breakeven", "tips")):
        return "rates"
    if any(w in t for w in ("oas", "spread", "high yield", "corporate")):
        return "credit"
    if any(w in t for w in ("vix", "volatility")):
        return "vol"
    if any(w in t for w in ("dollar", "exchange", "/")):
        return "fx"
    if any(w in t for w in ("oil", "gold", "copper", "commodity")):
        return "commodity"
    if any(w in t for w in ("s&p", "nasdaq", "dow", "wilshire")):
        return "equity"
    if any(w in t for w in ("walcl", "assets", "reverse repo", "treasury general", "m2", "reserve balances")):
        return "liquidity"
    if any(w in t for w in ("cpi", "price index", "inflation", "pce")):
        return "inflation"
    if any(w in t for w in ("unemployment", "payroll", "claims", "labor", "employment")):
        return "labour"
    if any(w in t for w in ("gdp", "production", "retail", "sales", "housing", "starts", "permits")):
        return "activity"
    return "macro"
