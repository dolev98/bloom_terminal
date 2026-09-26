"""Bank of Israel SDMX adapter (keyless). Verified live 2026-09-22.

Key format: "FLOW/SERIES_CODE" e.g. "EXR/RER_USD_ILS" (USD/ILS representative rate, daily), "BR/MNT_RIB_BOI_D" (policy rate).
Discover live keys with /availability/dataflow/BOI.STATISTICS/{FLOW}/1.0/*.
"""

from __future__ import annotations

import csv
import io
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

BASE = "https://edge.boi.org.il/FusionEdgeServer/sdmx/v2"
_RER_RE = re.compile(r"^RER_([A-Z]{3})_ILS$")


def _plain_name(flow: str, code: str) -> str:
    """Readable default name; the user can still edit it before saving."""
    m = _RER_RE.match(code)
    if flow == "EXR" and m:
        return f"{m.group(1)}/ILS representative exchange rate"
    return f"Bank of Israel {flow} {code}"


_URL_RE = re.compile(r"BOI\.STATISTICS/([A-Z0-9_]+)/1\.0/([A-Za-z0-9_.]+)")


@dataclass
class BoiProvider(Provider):
    id: str = "boi"
    name: str = "Bank of Israel (SDMX)"
    capabilities: Capability = Capability.SERIES | Capability.SEARCH
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_second=2, per_minute=60, concurrency=2))
    license: LicenseSpec = field(default_factory=lambda: LicenseSpec(grey=False, attribution="Source: Bank of Israel"))
    egress: str = "il"

    def parse_url(self, url: str) -> str | None:
        m = _URL_RE.search(url)
        return f"{m.group(1)}/{m.group(2)}" if m else None

    @staticmethod
    def split_key(key: str) -> tuple[str, str]:
        if "/" not in key:
            raise ProviderError(f"BOI key must be FLOW/SERIES, got {key!r}")
        flow, code = key.split("/", 1)
        return flow, code

    async def _csv(self, url: str, **params) -> list[dict]:
        client = get_client()
        async for attempt in retrying():
            with attempt:
                resp = await client.get(url, params=params, headers={"Accept": "text/csv"})
                raise_for_retry(resp)
        text = resp.text
        reader = csv.DictReader(io.StringIO(text))
        return list(reader)

    async def describe(self, key: str) -> SeriesSpec:
        flow, code = self.split_key(key)
        kind, unit, cat = _classify(flow, code)
        return SeriesSpec(
            series_id=f"boi:{key}",
            provider="boi",
            provider_key=key,
            name=_plain_name(flow, code),
            freq=_guess_freq(code),
            unit=unit,
            value_kind=kind,
            default_transform={"yield": "diff_bp", "price": "log_ret", "level_index": "yoy", "stock": "yoy"}.get(kind, "level"),
            country="IL",
            category=cat,
            license_note="Bank of Israel open data",
        )

    async def search(self, q: str) -> list[SeriesSpec]:
        """List available series codes in a dataflow (q = flow name, e.g. 'EXR')."""
        flow = q.strip().upper()
        rows = await self._csv(f"{BASE}/data/dataflow/BOI.STATISTICS/{flow}/1.0/*", format="csv", lastNObservations=1)
        seen: dict[str, SeriesSpec] = {}
        for r in rows:
            code = r.get("SERIES_CODE") or r.get("SERIES_ID") or ""
            if code and code not in seen:
                seen[code] = await self.describe(f"{flow}/{code}")
        return list(seen.values())[:200]

    async def get_series(self, spec: SeriesSpec, since: date | None = None, vintage: date | None = None) -> pl.DataFrame:
        flow, code = self.split_key(spec.provider_key)
        params = {"format": "csv"}
        if since:
            params["startperiod"] = since.isoformat()
        rows = await self._csv(f"{BASE}/data/dataflow/BOI.STATISTICS/{flow}/1.0/{code}", **params)
        if not rows:
            return empty_observations()
        ts, vals = [], []
        for r in rows:
            t = r.get("TIME_PERIOD") or r.get("TIME") or ""
            v = r.get("OBS_VALUE")
            if not t or v in (None, "", "NaN"):
                continue
            ts.append(_parse_period(t))
            vals.append(float(v))
        if not ts:
            return empty_observations()
        return pl.DataFrame({"ts": ts, "value": vals}).with_columns(pl.col("ts").cast(pl.Datetime("us"))).sort("ts")

    async def health(self) -> dict:
        try:
            rows = await self._csv(f"{BASE}/data/dataflow/BOI.STATISTICS/BR/1.0/MNT_RIB_BOI_D", format="csv", lastNObservations=1)
            return {"ok": bool(rows), "last": rows[-1].get("OBS_VALUE") if rows else None}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}


def _parse_period(t: str) -> date:
    t = t.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        return date.fromisoformat(t)
    if re.fullmatch(r"\d{4}-\d{2}", t):
        y, m = t.split("-")
        return date(int(y), int(m), 1)
    if re.fullmatch(r"\d{4}-Q[1-4]", t):
        y, q = t.split("-Q")
        return date(int(y), (int(q) - 1) * 3 + 1, 1)
    if re.fullmatch(r"\d{4}", t):
        return date(int(t), 1, 1)
    raise ProviderError(f"BOI: unknown period format {t!r}")


def _guess_freq(code: str) -> str:
    c = code.upper()
    if c.endswith("_D") or "RER_" in c:
        return "1d"
    if c.endswith("_M") or c.endswith("_MA") or "_M_" in c:
        return "1mo"
    if c.endswith("_Q") or "_Q_" in c:
        return "1q"
    return "1d"


def _classify(flow: str, code: str) -> tuple[str, str, str]:
    f = flow.upper()
    if f == "EXR":
        return "price", "ILS", "fx"
    if f in ("BR", "TLB"):
        return "yield", "pct", "rates"
    if f in ("ZCM", "SECDWH"):
        return "yield", "pct", "rates"
    if f == "PRI":
        return "level_index", "index", "inflation"
    if f == "NA":
        return "flow", "ILS mn", "activity"
    if f == "LBM":
        return "survey", "pct", "labour"
    if f == "MAG":
        return "stock", "ILS mn", "liquidity"
    return "other", "", "israel"
