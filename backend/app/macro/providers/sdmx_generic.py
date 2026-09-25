"""One SDMX-CSV adapter for IMF, BIS, ECB, Eurostat and OECD (plain httpx; the CSV dialects differ only in URL/headers).

Keys: "<flow>/<series key>" e.g. imf:CPI/USA.CPI._T.IX.M, bis:WS_CBPOL/M.US, ecb:FM/B.U2.EUR.4F.KR.DFR.LEV,
estat:prc_hicp_manr/M.RCH_A.CP00.EA20, oecd:OECD.SDD.STES,DSD_STES@DF_CLI,4.1/USA.M.LI...AA...H
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

_PERIOD_RE = {
    "ymd": re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"),
    "ym": re.compile(r"^(\d{4})-?M?(\d{2})$"),
    "yq": re.compile(r"^(\d{4})-?Q([1-4])$"),
    "ys": re.compile(r"^(\d{4})-?S([12])$"),
    "y": re.compile(r"^(\d{4})$"),
}


def parse_period(t: str) -> date:
    t = t.strip()
    if m := _PERIOD_RE["ymd"].match(t):
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if m := _PERIOD_RE["yq"].match(t):
        return date(int(m.group(1)), (int(m.group(2)) - 1) * 3 + 1, 1)
    if m := _PERIOD_RE["ys"].match(t):
        return date(int(m.group(1)), (int(m.group(2)) - 1) * 6 + 1, 1)
    if m := _PERIOD_RE["ym"].match(t):
        return date(int(m.group(1)), int(m.group(2)), 1)
    if m := _PERIOD_RE["y"].match(t):
        return date(int(m.group(1)), 1, 1)
    raise ProviderError(f"SDMX: unknown TIME_PERIOD {t!r}")


def parse_sdmx_csv(text: str) -> pl.DataFrame:
    """Any SDMX-CSV flavour: needs TIME_PERIOD + OBS_VALUE columns (case-insensitive). Later rows win on equal periods."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return empty_observations()
    cols = {c.strip().upper(): c for c in reader.fieldnames}
    tcol, vcol = cols.get("TIME_PERIOD"), cols.get("OBS_VALUE")
    if not tcol or not vcol:
        return empty_observations()
    obs: dict[date, float] = {}
    for r in reader:
        t, v = (r.get(tcol) or "").strip(), (r.get(vcol) or "").strip()
        if not t or v in ("", "NaN", "null", ":"):
            continue
        try:
            obs[parse_period(t)] = float(v)
        except (ValueError, ProviderError):
            continue
    if not obs:
        return empty_observations()
    ts = sorted(obs)
    return pl.DataFrame({"ts": ts, "value": [obs[t] for t in ts]}).with_columns(
        pl.col("ts").cast(pl.Datetime("us"))
    )


def _freq_from_key(key: str) -> str:
    parts = re.split(r"[./]", key)
    letters = {p for p in parts if len(p) == 1}
    if "M" in letters:
        return "1mo"
    if "Q" in letters:
        return "1q"
    if "A" in letters:
        return "1y"
    if "D" in letters or "B" in letters:
        return "1d"
    return "1mo"


@dataclass
class SdmxCsvProvider(Provider):
    """Base: subclasses set id/name/base + `request(flow, key, since)` -> (url, params, headers)."""

    capabilities: Capability = Capability.SERIES
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_second=2, per_minute=30, concurrency=2))
    license: LicenseSpec = field(default_factory=lambda: LicenseSpec(grey=False))
    url_re: str = ""

    def split_key(self, key: str) -> tuple[str, str]:
        if "/" not in key:
            raise ProviderError(f"{self.id}: key must be FLOW/SERIES_KEY, got {key!r}")
        flow, k = key.split("/", 1)
        return flow, k

    def request(
        self, flow: str, key: str, since: date | None
    ) -> tuple[str, dict, dict]:  # pragma: no cover - abstract
        raise NotImplementedError

    def parse_url(self, url: str) -> str | None:
        if not self.url_re:
            return None
        m = re.search(self.url_re, url)
        return f"{m.group(1)}/{m.group(2)}" if m else None

    async def describe(self, key: str) -> SeriesSpec:
        flow, k = self.split_key(key)
        return SeriesSpec(
            series_id=f"{self.id}:{key}",
            provider=self.id,
            provider_key=key,
            name=f"{self.name} {flow} {k}",
            freq=_freq_from_key(k),
            value_kind="other",
            default_transform="level",
            category="macro",
            license_note=self.license.note,
        )

    async def get_series(
        self, spec: SeriesSpec, since: date | None = None, vintage: date | None = None
    ) -> pl.DataFrame:
        flow, k = self.split_key(spec.provider_key)
        url, params, headers = self.request(flow, k, since)
        client = get_client()
        async for attempt in retrying():
            with attempt:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 404:
                    return empty_observations()  # NoResultsFound
                raise_for_retry(resp)
        return parse_sdmx_csv(resp.text)

    async def health(self) -> dict:
        return {"ok": True, "note": "keyless SDMX-CSV"}


@dataclass
class ImfProvider(SdmxCsvProvider):
    id: str = "imf"
    name: str = "IMF SDMX 2.1"
    url_re: str = r"api\.imf\.org/external/sdmx/2\.1/data/([A-Za-z0-9_]+)/([A-Za-z0-9_.+*]+)"
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(
            grey=False, attribution="Source: IMF Data", note="IMF data: free with attribution"
        )
    )

    def request(self, flow: str, key: str, since: date | None) -> tuple[str, dict, dict]:
        params = {"startPeriod": str(since.year)} if since else {}
        return (
            f"https://api.imf.org/external/sdmx/2.1/data/{flow}/{key}",
            params,
            {"Accept": "application/vnd.sdmx.data+csv;version=1.0.0"},
        )


@dataclass
class BisProvider(SdmxCsvProvider):
    id: str = "bis"
    name: str = "BIS statistics"
    url_re: str = r"stats\.bis\.org/api/v2/data/dataflow/BIS/([A-Z0-9_]+)/1\.0/([A-Za-z0-9_.+*]+)"
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(
            grey=False, attribution="Source: BIS", note="BIS: free, attribution"
        )
    )

    def request(self, flow: str, key: str, since: date | None) -> tuple[str, dict, dict]:
        params = {"format": "csv"}
        if since:
            params["startPeriod"] = f"{since.year}-{since.month:02d}"
        return (
            f"https://stats.bis.org/api/v2/data/dataflow/BIS/{flow}/1.0/{key}",
            params,
            {"Accept": "text/csv"},
        )


@dataclass
class EcbProvider(SdmxCsvProvider):
    id: str = "ecb"
    name: str = "ECB Data Portal"
    url_re: str = r"data-api\.ecb\.europa\.eu/service/data/([A-Z0-9_]+)/([A-Za-z0-9_.+*]+)"
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(
            grey=False, attribution="Source: ECB", note="ECB data: free, attribution"
        )
    )

    def request(self, flow: str, key: str, since: date | None) -> tuple[str, dict, dict]:
        params = {"format": "csvdata"}
        if since:
            params["startPeriod"] = since.isoformat()
        return f"https://data-api.ecb.europa.eu/service/data/{flow}/{key}", params, {"Accept": "text/csv"}


@dataclass
class EstatProvider(SdmxCsvProvider):
    id: str = "estat"
    name: str = "Eurostat"
    url_re: str = (
        r"ec\.europa\.eu/eurostat/api/dissemination/sdmx/2\.1/data/([a-z0-9_]+)/([A-Za-z0-9_.+*\-]+)"
    )
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(
            grey=False, attribution="Source: Eurostat", note="Eurostat: free reuse (CC BY 4.0)"
        )
    )

    def request(self, flow: str, key: str, since: date | None) -> tuple[str, dict, dict]:
        # Eurostat answers 406 to `Accept: text/csv`; it only serves SDMX-CSV under its registered media type.
        params = {"format": "SDMX-CSV"}
        if since:
            params["startPeriod"] = f"{since.year}-{since.month:02d}"
        return (
            f"https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/{flow}/{key}",
            params,
            {"Accept": "application/vnd.sdmx.data+csv;version=1.0.0"},
        )


@dataclass
class OecdProvider(SdmxCsvProvider):
    id: str = "oecd"
    name: str = "OECD Data Explorer"
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_minute=1, burst=5, concurrency=1))  # 60/hour
    url_re: str = r"sdmx\.oecd\.org/public/rest/data/([A-Za-z0-9_.,@]+)/([A-Za-z0-9_.+*\-]+)"
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(
            grey=False, attribution="Source: OECD", note="OECD: free, attribution; 60 req/hour"
        )
    )

    def request(self, flow: str, key: str, since: date | None) -> tuple[str, dict, dict]:
        # The OECD endpoint returns HTTP 500 for `format=csvfile` and for a query without any parameter, so ask for
        # SDMX-CSV by media type and always bound the query with startPeriod.
        params = {"startPeriod": str(since.year) if since else "1950"}
        return (
            f"https://sdmx.oecd.org/public/rest/data/{flow}/{key}",
            params,
            {"Accept": "application/vnd.sdmx.data+csv; charset=utf-8"},
        )
