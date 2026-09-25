"""Israel: CBS release-timing rule engine (CPI/PPI/housing on the 15th, 18:30 IL), TASE reporting deadlines and TASE closure days."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.calendar.dictionary import load_dictionary
from app.calendar.rules import (
    IsraelCalendar,
    cbs_release_ts,
    local_ts,
    month_range,
    quarter_range,
    tase_reporting_deadlines,
)
from app.calendar.types import NormalizedEvent
from app.data.providers.base import Holiday

log = logging.getLogger(__name__)

CBS_URL = "https://www.cbs.gov.il/en/Pages/default.aspx"
CBS_MONTHLY = (
    # event_key, nominal day, price-index rule?, importance
    ("il.cpi", 15, True, 3),
    ("il.ppi", 15, True, 1),
    ("il.housing_index", 15, True, 2),
)
CBS_QUARTERLY_GDP_DAY = 16  # first GDP estimate ~ mid-month after quarter end (Feb/May/Aug/Nov), 13:00 IL


async def israel_holidays(years: list[int]) -> list[Holiday]:
    """Hebcal holidays for the given years via the registry (empty on failure)."""
    from app.data.registry import get_registry

    reg = get_registry()
    try:
        heb = reg.get("hebcal")
    except Exception:
        return []
    out: list[Holiday] = []
    for y in years:
        try:
            out.extend(
                await reg.call(heb, "get_holidays", lambda y=y: heb.get_holidays("IL", y), key=f"IL:{y}")
            )
        except Exception as e:
            log.warning("hebcal %s failed: %s", y, e)
    return out


def israel_calendar(holidays: list[Holiday]) -> IsraelCalendar:
    closed = {
        h.date
        for h in holidays
        if h.exchange_closed and not h.name.startswith("Erev") and h.name != "Yom HaZikaron"
    }
    eves = {h.date for h in holidays if h.name.startswith("Erev") or h.name == "Yom HaZikaron"}
    # the day before a closure day is a short day as well
    eves |= {d - timedelta(days=1) for d in closed}
    return IsraelCalendar(holidays=frozenset(closed), eves=frozenset(eves))


@dataclass
class CbsRulesSource:
    id: str = "cbs_rules"
    tier: str = "rule"
    holidays: list[Holiday] | None = None  # injectable for tests

    async def fetch(self, start: date, end: date) -> list[NormalizedEvent]:
        years = sorted({start.year, end.year, end.year + 1})
        hol = self.holidays if self.holidays is not None else await israel_holidays(years)
        cal = israel_calendar(hol)
        dic = load_dictionary()
        out: list[NormalizedEvent] = []
        for y, m in month_range(start, end + timedelta(days=31)):
            for key, day, price, imp in CBS_MONTHLY:
                ind = dic.by_key.get(key)
                ts = cbs_release_ts(date(y, m, day), cal, price_index=price)
                if not (start <= ts.date() <= end):
                    continue
                prev = date(y, m, 1) - timedelta(days=1)
                out.append(
                    NormalizedEvent(
                        event_key=key,
                        kind="macro",
                        country="IL",
                        currency="ILS",
                        title=ind.title if ind else key,
                        ts=ts,
                        importance=imp,
                        category=ind.category if ind else "inflation",
                        unit=ind.unit if ind else "%",
                        reference_period=f"{prev.year}-{prev.month:02d}",
                        source=self.id,
                        source_url=CBS_URL,
                        linked_series=list(ind.linked_series) if ind else [],
                        notes="rule: 15th 18:30 IL; Fri/Sat/holiday-eve -> preceding eligible day 14:00",
                        tier=self.tier,
                    )
                )
        for y, q in quarter_range(start - timedelta(days=60), end):
            # flash GDP for quarter q is published in the middle of the second month after quarter end
            rel_month = q * 3 + 2
            ry = y
            if rel_month > 12:
                rel_month -= 12
                ry += 1
            ts = cbs_release_ts(date(ry, rel_month, CBS_QUARTERLY_GDP_DAY), cal, price_index=False)
            if not (start <= ts.date() <= end):
                continue
            ind = dic.by_key.get("il.gdp")
            out.append(
                NormalizedEvent(
                    event_key="il.gdp",
                    kind="macro",
                    country="IL",
                    currency="ILS",
                    title=ind.title if ind else "Israel GDP flash",
                    ts=ts,
                    importance=2,
                    category="growth",
                    unit="%",
                    reference_period=f"{y}Q{q}",
                    source=self.id,
                    source_url=CBS_URL,
                    notes="rule: first estimate mid second month after quarter end, 13:00 IL (unverified)",
                    tier=self.tier,
                    verified=False,
                    tags=["unverified"],
                )
            )
        return out


async def tase_watchlist_tickers() -> list[str]:
    try:
        from app.api.routers.watchlists import all_tickers

        return sorted({t.upper() for t in await all_tickers() if t.upper().endswith(".TA")})
    except Exception as e:  # pragma: no cover - watchlists table missing in odd setups
        log.debug("watchlist tickers unavailable: %s", e)
        return []


@dataclass
class TaseSource:
    id: str = "tase"
    tier: str = "rule"
    holidays: list[Holiday] | None = None
    tickers: list[str] | None = None
    extra: list[str] = field(default_factory=list)

    async def fetch(self, start: date, end: date) -> list[NormalizedEvent]:
        out: list[NormalizedEvent] = []
        tickers = self.tickers if self.tickers is not None else await tase_watchlist_tickers()
        for y in sorted({start.year, end.year}):
            for dl, period, label in tase_reporting_deadlines(y):
                if not (start <= dl <= end):
                    continue
                out.append(
                    NormalizedEvent(
                        event_key=f"il.tase_deadline.{period.lower()}",
                        kind="reporting_deadline",
                        country="IL",
                        currency="ILS",
                        title=f"TASE statutory deadline: {label}",
                        ts=local_ts(dl, "23:59", "Asia/Jerusalem"),
                        importance=1,
                        category="corporate",
                        reference_period=f"{y}{period}" if period != "FY" else f"FY{y - 1}",
                        source=self.id,
                        source_url="https://www.isa.gov.il/",
                        affected_tickers=list(tickers),
                        notes="Securities Regulations (periodic and immediate reports): annual by 31 Mar; quarterly within 60 days",
                        tier=self.tier,
                    )
                )
        hol = (
            self.holidays
            if self.holidays is not None
            else await israel_holidays(sorted({start.year, end.year}))
        )
        for h in hol:
            if h.exchange_closed and start <= h.date <= end:
                out.append(
                    NormalizedEvent(
                        event_key=f"il.holiday.{h.date.isoformat()}",
                        kind="holiday",
                        country="IL",
                        currency="ILS",
                        title=f"TASE closed: {h.name}",
                        ts=local_ts(h.date, "00:00", "Asia/Jerusalem"),
                        importance=1,
                        category="other",
                        reference_period=h.date.isoformat(),
                        source=self.id,
                        source_url="https://www.hebcal.com/",
                        notes="Holiday data © Hebcal.com, CC BY 4.0",
                        tier=self.tier,
                    )
                )
        return out
