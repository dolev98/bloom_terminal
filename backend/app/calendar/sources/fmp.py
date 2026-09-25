"""Financial Modeling Prep (stable API): economic calendar (consensus/actual layer) + earnings / dividends / IPO calendars. Needs settings.fmp_api_key."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.calendar.dictionary import fix_period_year, load_dictionary, period_from_text
from app.calendar.types import NormalizedEvent, dedupe_events
from app.data.http import get_client
from app.data.retry import raise_for_retry, retrying

log = logging.getLogger(__name__)
BASE = "https://financialmodelingprep.com/stable"
MAX_WINDOW_DAYS = 90
_IMPACT = {"high": 3, "medium": 2, "low": 1}
_PAREN_RE = re.compile(r"^(.*?)\s*\(([^)]*)\)\s*$")


def _key() -> str:
    from app.core.config import get_settings

    return get_settings().fmp_api_key or ""


def _f(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse_ts(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)  # FMP economic timestamps are UTC
    return dt


def _windows(start: date, end: date) -> list[tuple[date, date]]:
    out = []
    a = start
    while a <= end:
        b = min(end, a + timedelta(days=MAX_WINDOW_DAYS - 1))
        out.append((a, b))
        a = b + timedelta(days=1)
    return out


async def fmp_get(path: str, **params) -> list[dict]:
    key = _key()
    if not key:
        return []
    client = get_client()
    async for attempt in retrying(attempts=2):
        with attempt:
            resp = await client.get(f"{BASE}/{path}", params={**params, "apikey": key})
            if resp.status_code in (401, 402, 403):
                raise RuntimeError(f"FMP {resp.status_code}: {resp.text[:120]}")
            raise_for_retry(resp)
    data = resp.json()
    if isinstance(data, dict) and ("Error Message" in data or "error" in data):
        raise RuntimeError(f"FMP: {data}")
    return data if isinstance(data, list) else []


def parse_economic(
    rows: list[dict], start: date, end: date, source: str = "fmp_econ", tier: str = "vendor"
) -> list[NormalizedEvent]:
    dic = load_dictionary()
    out: list[NormalizedEvent] = []
    for r in rows:
        ts = _parse_ts(r.get("date"))
        if ts is None or not (start <= ts.date() <= end):
            continue
        cc = dic.country_from_name(r.get("country")) or dic.country_from_currency(r.get("currency"))
        if not cc:
            continue
        raw_name = str(r.get("event") or "").strip()
        m = _PAREN_RE.match(raw_name)
        name, period_txt = (m.group(1), m.group(2)) if m else (raw_name, "")
        period = fix_period_year(period_from_text(period_txt, None) if period_txt else "", ts.date())
        ind = dic.lookup(name, cc)
        actual = _f(r.get("actual"))
        consensus = _f(r.get("estimate"))
        out.append(
            NormalizedEvent(
                event_key=ind.event_key if ind else dic.fallback_key(name, cc),
                kind=ind.kind if ind else "macro",
                country=cc,
                currency=str(r.get("currency") or "").upper() or None,
                title=ind.title if ind else name,
                ts=ts,
                importance=ind.importance if ind else _IMPACT.get(str(r.get("impact") or "low").lower(), 1),
                category=ind.category if ind else "other",
                unit=ind.unit if ind else (r.get("unit") or None),
                reference_period=period,
                status="released" if actual is not None else "scheduled",
                actual=actual,
                actual_source="fmp" if actual is not None else None,
                consensus=consensus,
                consensus_source="fmp" if consensus is not None else None,
                previous=_f(r.get("previous")),
                source=source,
                source_url="https://financialmodelingprep.com/",
                linked_series=list(ind.linked_series) if ind else [],
                tier=tier,
                raw={"event": raw_name, "impact": r.get("impact"), "change": r.get("change")},
            )
        )
    return out


@dataclass
class FmpEconomicSource:
    id: str = "fmp_econ"
    tier: str = "vendor"

    async def fetch(self, start: date, end: date) -> list[NormalizedEvent]:
        if not _key():
            log.debug("fmp: no api key; skipping")
            return []
        rows: list[dict] = []
        for a, b in _windows(start, end):
            rows += await fmp_get("economic-calendar", **{"from": a.isoformat(), "to": b.isoformat()})
        return dedupe_events(parse_economic(rows, start, end, self.id, self.tier))


def _earn_ts(d: date, when: str | None) -> datetime:
    from app.calendar.rules import local_ts

    w = (when or "").lower()
    return local_ts(d, "07:00" if w == "bmo" else "16:30" if w == "amc" else "12:00", "America/New_York")


def parse_corporate(
    kind: str,
    rows: list[dict],
    start: date,
    end: date,
    tickers: set[str] | None,
    source: str = "fmp_corp",
    tier: str = "vendor",
) -> list[NormalizedEvent]:
    out: list[NormalizedEvent] = []
    for r in rows:
        sym = str(r.get("symbol") or "").upper()
        try:
            d = date.fromisoformat(str(r.get("date"))[:10])
        except ValueError:
            continue
        if not sym or not (start <= d <= end) or (tickers is not None and sym not in tickers):
            continue
        if kind == "earnings":
            actual, cons = _f(r.get("epsActual")), _f(r.get("epsEstimated"))
            out.append(
                NormalizedEvent(
                    event_key=f"earnings.{sym}",
                    kind="earnings",
                    country="US",
                    currency="USD",
                    title=f"{sym} earnings",
                    ts=_earn_ts(d, r.get("time")),
                    importance=2,
                    category="corporate",
                    unit="EPS",
                    reference_period=d.isoformat(),
                    status="released" if actual is not None else "scheduled",
                    actual=actual,
                    actual_source="fmp" if actual is not None else None,
                    consensus=cons,
                    consensus_source="fmp" if cons is not None else None,
                    forecast_alt=_f(r.get("revenueEstimated")),
                    affected_tickers=[sym],
                    source=source,
                    source_url="https://financialmodelingprep.com/",
                    tier=tier,
                    raw={k: r.get(k) for k in ("revenueActual", "revenueEstimated", "time", "lastUpdated")},
                )
            )
        elif kind == "dividend":
            out.append(
                NormalizedEvent(
                    event_key=f"dividend.{sym}",
                    kind="dividend",
                    country="US",
                    currency="USD",
                    title=f"{sym} ex-dividend {r.get('dividend') or r.get('adjDividend') or ''}".strip(),
                    ts=_earn_ts(d, "bmo"),
                    importance=1,
                    category="corporate",
                    unit="USD",
                    reference_period=d.isoformat(),
                    actual=_f(r.get("dividend") or r.get("adjDividend")),
                    affected_tickers=[sym],
                    source=source,
                    tier=tier,
                    notes=f"record {r.get('recordDate') or '?'}; payment {r.get('paymentDate') or '?'}",
                    raw={
                        k: r.get(k)
                        for k in ("recordDate", "paymentDate", "declarationDate", "yield", "frequency")
                    },
                )
            )
        elif kind == "ipo":
            out.append(
                NormalizedEvent(
                    event_key=f"ipo.{sym}",
                    kind="ipo",
                    country="US",
                    currency="USD",
                    title=f"IPO: {r.get('company') or sym} ({sym}) {r.get('priceRange') or ''}".strip(),
                    ts=_earn_ts(d, "bmo"),
                    importance=1,
                    category="corporate",
                    reference_period=d.isoformat(),
                    affected_tickers=[sym],
                    source=source,
                    tier=tier,
                    notes=f"{r.get('exchange') or ''} {r.get('actions') or ''}".strip() or None,
                    raw={k: r.get(k) for k in ("exchange", "shares", "priceRange", "marketCap", "actions")},
                )
            )
    return out


@dataclass
class FmpCorporateSource:
    id: str = "fmp_corp"
    tier: str = "vendor"
    tickers: list[str] | None = None  # None -> watchlist tickers

    async def fetch(self, start: date, end: date) -> list[NormalizedEvent]:
        if not _key():
            return []
        tickers = self.tickers
        if tickers is None:
            from app.api.routers.watchlists import all_tickers

            tickers = [t.upper() for t in await all_tickers()]
        tset = set(tickers)
        out: list[NormalizedEvent] = []
        for a, b in _windows(start, end):
            params = {"from": a.isoformat(), "to": b.isoformat()}
            for kind, path in (
                ("earnings", "earnings-calendar"),
                ("dividend", "dividends-calendar"),
                ("ipo", "ipos-calendar"),
            ):
                try:
                    rows = await fmp_get(path, **params)
                except Exception as e:
                    log.info("fmp %s failed: %s", path, e)
                    continue
                out += parse_corporate(kind, rows, a, b, tset if kind != "ipo" else None, self.id, self.tier)
        return out
