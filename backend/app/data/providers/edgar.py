"""SEC EDGAR adapter (data.sec.gov JSON APIs). Free, no key, fair-access 10 req/s, User-Agent "Name email" mandatory.

M0 scope: ticker->CIK map, submissions (filings list), companyfacts, and XBRL concept series (`edgar:AAPL:Revenues`).
Statement normalization via edgartools lands in M2.
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

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANYFACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
COMPANYCONCEPT = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/{taxonomy}/{concept}.json"
_URL_RE = re.compile(r"CIK=?(\d{1,10})", re.I)


@dataclass
class EdgarProvider(Provider):
    id: str = "edgar"
    name: str = "SEC EDGAR"
    capabilities: Capability = Capability.SERIES | Capability.SEARCH | Capability.STATEMENTS
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_second=8, concurrency=4))
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(grey=False, attribution="Source: SEC EDGAR")
    )
    requires: tuple[str, ...] = ("sec_user_agent",)
    _ticker_map: dict[str, dict] = field(default_factory=dict)

    async def _json(self, url: str) -> dict:
        client = get_client()
        async for attempt in retrying():
            with attempt:
                resp = await client.get(url, headers={"Accept": "application/json"})
                if resp.status_code == 403:
                    raise ProviderError("SEC 403: declare TERMINAL_SEC_USER_AGENT as 'Name email'")
                raise_for_retry(resp)
        return resp.json()

    async def ticker_map(self) -> dict[str, dict]:
        if not self._ticker_map:
            data = await self._json(TICKERS_URL)
            self._ticker_map = {
                v["ticker"].upper(): {"cik": int(v["cik_str"]), "name": v["title"]} for v in data.values()
            }
        return self._ticker_map

    async def resolve_cik(self, ticker_or_cik: str) -> int:
        t = ticker_or_cik.strip().upper()
        if t.isdigit():
            return int(t)
        m = await self.ticker_map()
        if t not in m:
            raise ProviderError(f"EDGAR: unknown ticker {t}")
        return m[t]["cik"]

    async def submissions(self, ticker_or_cik: str) -> dict:
        cik = await self.resolve_cik(ticker_or_cik)
        return await self._json(SUBMISSIONS.format(cik=cik))

    async def recent_filings(
        self, ticker_or_cik: str, forms: set[str] | None = None, limit: int = 50
    ) -> list[dict]:
        sub = await self.submissions(ticker_or_cik)
        rec = sub.get("filings", {}).get("recent", {})
        out = []
        for i, form in enumerate(rec.get("form", [])):
            if forms and form not in forms:
                continue
            out.append(
                {
                    "form": form,
                    "accession": rec["accessionNumber"][i],
                    "filed": rec["filingDate"][i],
                    "report_date": rec.get("reportDate", [None] * len(rec["form"]))[i],
                    "primary_doc": rec.get("primaryDocument", [None] * len(rec["form"]))[i],
                    "items": rec.get("items", [""] * len(rec["form"]))[i],
                    "cik": sub.get("cik"),
                }
            )
            if len(out) >= limit:
                break
        return out

    async def companyfacts(self, ticker_or_cik: str) -> dict:
        cik = await self.resolve_cik(ticker_or_cik)
        return await self._json(COMPANYFACTS.format(cik=cik))

    def parse_url(self, url: str) -> str | None:
        m = _URL_RE.search(url)
        return m.group(1) if m else None

    async def search(self, q: str) -> list[SeriesSpec]:
        m = await self.ticker_map()
        ql = q.strip().lower()
        hits = [(t, v) for t, v in m.items() if ql in t.lower() or ql in v["name"].lower()][:20]
        return [
            SeriesSpec(
                series_id=f"edgar:{t}:Revenues",
                provider="edgar",
                provider_key=t,
                field_name="Revenues",
                name=v["name"],
                freq="1q",
                unit="USD",
                value_kind="flow",
                default_transform="yoy",
                country="US",
                category="fundamentals",
            )
            for t, v in hits
        ]

    async def describe(self, key: str) -> SeriesSpec:
        ticker, _, concept = key.partition(":")
        concept = concept or "Revenues"
        m = await self.ticker_map()
        name = m.get(ticker.upper(), {}).get("name", ticker)
        return SeriesSpec(
            series_id=f"edgar:{ticker.upper()}:{concept}",
            provider="edgar",
            provider_key=ticker.upper(),
            field_name=concept,
            name=f"{name} — {concept}",
            freq="1q",
            unit="USD",
            value_kind="flow",
            default_transform="yoy",
            country="US",
            category="fundamentals",
            publication_lag_days=40,
        )

    async def get_series(
        self, spec: SeriesSpec, since: date | None = None, vintage: date | None = None
    ) -> pl.DataFrame:
        """XBRL concept as a series: quarterly (3-month) values from companyconcept, keyed by period end."""
        concept = spec.field_name or "Revenues"
        taxonomy = spec.params.get("taxonomy", "us-gaap")
        cik = await self.resolve_cik(spec.provider_key)
        data = await self._json(COMPANYCONCEPT.format(cik=cik, taxonomy=taxonomy, concept=concept))
        units = data.get("units", {})
        unit_key = spec.params.get("unit") or next(iter(units), None)
        if not unit_key:
            return empty_observations()
        rows = []
        for f in units[unit_key]:
            start, end = f.get("start"), f.get("end")
            if not end:
                continue
            # keep ~quarterly durations (or instants when no start)
            if start:
                d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
                days = (d1 - d0).days
                if not (80 <= days <= 100):
                    continue
            rows.append((end, f["val"], f.get("filed", "")))
        if not rows:
            return empty_observations()
        # point-in-time: earliest filed value per period end (as-first-reported)
        best: dict[str, tuple[float, str]] = {}
        for end, val, filed in rows:
            if end not in best or filed < best[end][1]:
                best[end] = (float(val), filed)
        ts = sorted(best)
        df = pl.DataFrame({"ts": ts, "value": [best[t][0] for t in ts]})
        df = df.with_columns(pl.col("ts").str.strptime(pl.Datetime("us"), "%Y-%m-%d"))
        if since:
            df = df.filter(pl.col("ts") >= pl.datetime(since.year, since.month, since.day))
        return df

    async def health(self) -> dict:
        try:
            m = await self.ticker_map()
            return {"ok": len(m) > 1000, "tickers": len(m)}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
