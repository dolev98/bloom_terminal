"""Finnhub (free): earnings calendar for watchlist tickers + IPO calendar."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from app.calendar.rules import local_ts
from app.calendar.types import NormalizedEvent
from app.data.registry import get_registry

log = logging.getLogger(__name__)


def earnings_event(ev, source: str, tier: str) -> NormalizedEvent:
    d = ev.ts.date()
    det = ev.details or {}
    hour = (det.get("hour") or "").lower()
    ts = local_ts(d, "07:00" if hour == "bmo" else "16:30" if hour == "amc" else "12:00", "America/New_York")
    q, y = det.get("quarter"), det.get("year")
    period = f"{y}Q{q}" if q and y else d.isoformat()
    actual, cons = det.get("epsActual"), det.get("epsEstimate")
    return NormalizedEvent(
        event_key=f"earnings.{ev.ticker.upper()}",
        kind="earnings",
        country="IL" if ev.ticker.upper().endswith(".TA") else "US",
        currency="USD",
        title=f"{ev.ticker.upper()} earnings ({period})",
        ts=ts,
        importance=2,
        category="corporate",
        unit="EPS",
        reference_period=period,
        status="released" if actual is not None else "scheduled",
        actual=float(actual) if actual is not None else None,
        actual_source="finnhub" if actual is not None else None,
        consensus=float(cons) if cons is not None else None,
        consensus_source="finnhub" if cons is not None else None,
        forecast_alt=float(det["revenueEstimate"]) if det.get("revenueEstimate") is not None else None,
        affected_tickers=[ev.ticker.upper()],
        source=source,
        source_url="https://finnhub.io/",
        tier=tier,
        raw={
            "revenueActual": det.get("revenueActual"),
            "revenueEstimate": det.get("revenueEstimate"),
            "hour": hour,
        },
    )


def ipo_event(r: dict, source: str, tier: str) -> NormalizedEvent | None:
    try:
        d = date.fromisoformat(str(r.get("date"))[:10])
    except ValueError:
        return None
    sym = str(r.get("symbol") or r.get("name") or "").upper()
    if not sym:
        return None
    return NormalizedEvent(
        event_key=f"ipo.{sym}",
        kind="ipo",
        country="US",
        currency="USD",
        title=f"IPO: {r.get('name') or sym} ({sym}) {r.get('price') or ''} [{r.get('status') or ''}]",
        ts=local_ts(d, "09:30", "America/New_York"),
        importance=1,
        category="corporate",
        reference_period=d.isoformat(),
        affected_tickers=[sym] if r.get("symbol") else [],
        source=source,
        source_url="https://finnhub.io/",
        tier=tier,
        notes=f"{r.get('exchange') or ''}; shares {r.get('numberOfShares') or '?'}; value {r.get('totalSharesValue') or '?'}",
        raw={k: r.get(k) for k in ("exchange", "status", "price", "numberOfShares", "totalSharesValue")},
    )


@dataclass
class FinnhubCorporateSource:
    id: str = "finnhub_corp"
    tier: str = "vendor"
    tickers: list[str] | None = None

    async def fetch(self, start: date, end: date) -> list[NormalizedEvent]:
        reg = get_registry()
        try:
            fh = reg.get("finnhub")
        except Exception:
            return []
        if not reg.is_usable(fh)[0]:
            return []
        tickers = self.tickers
        if tickers is None:
            from app.api.routers.watchlists import all_tickers

            tickers = [t.upper() for t in await all_tickers() if not t.upper().endswith(".TA")]
        out: list[NormalizedEvent] = []
        if tickers:
            try:
                evs = await reg.call(
                    fh, "get_events", lambda: fh.get_events(tickers, start, end), key=",".join(tickers[:10])
                )
                out += [earnings_event(e, self.id, self.tier) for e in evs if e.kind == "earnings"]
            except Exception as e:
                log.info("finnhub earnings failed: %s", e)
        try:
            data = await reg.call(
                fh,
                "calendar_ipo",
                lambda: fh._get("calendar/ipo", **{"from": start.isoformat(), "to": end.isoformat()}),
                key="ipo",
            )
            for r in (data or {}).get("ipoCalendar", []):
                ev = ipo_event(r, self.id, self.tier)
                if ev and start <= ev.ts.date() <= end:
                    out.append(ev)
        except Exception as e:
            log.info("finnhub ipo failed: %s", e)
        return out
