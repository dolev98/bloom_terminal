"""TreasuryDirect auctions: upcoming schedule + results (high yield / bid-to-cover) once auctioned."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

from app.calendar.rules import local_ts
from app.calendar.types import NormalizedEvent
from app.data.http import get_client
from app.data.retry import raise_for_retry, retrying

log = logging.getLogger(__name__)
UPCOMING_URL = "https://www.treasurydirect.gov/TA_WS/securities/upcoming"
AUCTIONED_URL = "https://www.treasurydirect.gov/TA_WS/securities/auctioned"
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([AP]M)", re.I)


def _f(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _close_time(rec: dict) -> str:
    m = _TIME_RE.search(str(rec.get("closingTimeCompetitive") or ""))
    if not m:
        return "13:00" if (rec.get("securityType") or "").lower() != "bill" else "11:30"
    h, mi, ap = int(m.group(1)), m.group(2), m.group(3).upper()
    if ap == "PM" and h != 12:
        h += 12
    if ap == "AM" and h == 12:
        h = 0
    return f"{h:02d}:{mi}"


def _result(rec: dict) -> tuple[float | None, str]:
    stype = (rec.get("securityType") or rec.get("type") or "").lower()
    if stype == "bill":
        return _f(rec.get("highDiscountRate")), "high discount rate"
    if stype == "frn" or rec.get("type") == "FRN":
        return _f(rec.get("highDiscountMargin")), "high discount margin"
    return _f(rec.get("highYield")), "high yield"


def auction_event(rec: dict, source: str, tier: str) -> NormalizedEvent | None:
    try:
        d = date.fromisoformat(str(rec.get("auctionDate"))[:10])
    except ValueError:
        return None
    term = rec.get("securityTerm") or rec.get("term") or "?"
    stype = rec.get("securityType") or rec.get("type") or ""
    cusip = rec.get("cusip") or ""
    amt = _f(rec.get("offeringAmount"))
    amt_txt = f" ${amt / 1e9:.0f}bn" if amt else ""
    reopen = " (reopening)" if str(rec.get("reopening", "")).lower() == "yes" else ""
    actual, label = _result(rec)
    btc = _f(rec.get("bidToCoverRatio"))
    notes = []
    if btc:
        notes.append(f"bid-to-cover {btc:.2f}")
    acc = _f(rec.get("totalAccepted"))
    if acc:
        notes.append(f"accepted ${acc / 1e9:.1f}bn")
    for k, lbl in (
        ("indirectBidderAccepted", "indirect"),
        ("directBidderAccepted", "direct"),
        ("primaryDealerAccepted", "dealers"),
    ):
        v = _f(rec.get(k))
        if v and acc:
            notes.append(f"{lbl} {v / acc * 100:.0f}%")
    slug = re.sub(r"[^a-z0-9]+", "_", f"{term} {stype}".lower()).strip("_")
    # securityType says "Note" for TIPS and FRNs too; `type` tells them apart (a TIPS yield is a real yield,
    # an FRN result is a discount margin — neither is comparable with a nominal note's yield)
    kind = str(rec.get("type") or "").upper()
    shown = "TIPS" if kind == "TIPS" else "floating-rate note" if kind == "FRN" else stype
    return NormalizedEvent(
        event_key=f"us.auction.{slug}",
        kind="auction",
        country="US",
        currency="USD",
        title=f"{term} {shown} auction{amt_txt}{reopen}",
        ts=local_ts(d, _close_time(rec), "America/New_York"),
        importance=2 if stype.lower() in ("note", "bond", "tips") and "year" in term.lower() else 1,
        category="rates",
        unit="%",
        reference_period=cusip or d.isoformat(),
        status="released" if actual is not None else "scheduled",
        actual=actual,
        actual_source="treasurydirect" if actual is not None else None,
        forecast_alt=btc,
        source=source,
        source_url=f"https://www.treasurydirect.gov/auctions/announcements-data-results/?cusip={cusip}"
        if cusip
        else "https://www.treasurydirect.gov/auctions/upcoming/",
        notes=("; ".join(notes) + f" ({label})") if notes else None,
        tier=tier,
        raw={"cusip": cusip, "securityType": stype, "term": term, "bid_to_cover": btc},
    )


@dataclass
class TreasuryAuctionSource:
    id: str = "treasury"
    tier: str = "official"
    results_days: int = 14

    async def _get(self, url: str, **params) -> list[dict]:
        client = get_client()
        async for attempt in retrying(attempts=2):
            with attempt:
                resp = await client.get(url, params={"format": "json", **params})
                raise_for_retry(resp)
        data = resp.json()
        return data if isinstance(data, list) else []

    async def fetch(self, start: date, end: date) -> list[NormalizedEvent]:
        out: list[NormalizedEvent] = []
        try:
            recs = await self._get(UPCOMING_URL)
        except Exception as e:
            log.info("treasury upcoming unavailable: %s", e)
            recs = []
        try:
            recs += await self._get(AUCTIONED_URL, days=self.results_days)
        except Exception as e:
            log.info("treasury auctioned unavailable: %s", e)
        for rec in recs:
            ev = auction_event(rec, self.id, self.tier)
            if ev and start <= ev.ts.date() <= end:
                out.append(ev)
        return out
