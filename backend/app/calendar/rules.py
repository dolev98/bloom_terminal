"""Pure date rules: nth weekday, US OpEx / quad witching, FOMC minutes & Beige Book offsets, Israel CBS release timing, TASE deadlines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.calendar.types import to_utc_naive

IL_TZ = ZoneInfo("Asia/Jerusalem")
NY_TZ = ZoneInfo("America/New_York")


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th `weekday` (Mon=0) of a month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def third_friday(year: int, month: int) -> date:
    return nth_weekday(year, month, 4, 3)


def last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def local_ts(d: date, hhmm: str, tz: str) -> datetime:
    """Local wall time on `d` in `tz` -> naive UTC datetime."""
    h, m = (int(x) for x in hhmm.split(":"))
    return to_utc_naive(datetime.combine(d, time(h, m), tzinfo=ZoneInfo(tz)))


# --- US expiries ---------------------------------------------------------


def opex_dates(year: int, holidays: set[date]) -> list[tuple[date, bool]]:
    """Monthly options expiration: 3rd Friday (preceding Thursday if the Friday is an exchange holiday). Quad witching Mar/Jun/Sep/Dec."""
    out = []
    for month in range(1, 13):
        d = third_friday(year, month)
        while d in holidays or d.weekday() >= 5:
            d -= timedelta(days=1)
        out.append((d, month in (3, 6, 9, 12)))
    return out


def sp_rebalance_dates(year: int, holidays: set[date]) -> list[date]:
    return [d for d, quad in opex_dates(year, holidays) if quad]


def fomc_minutes_date(decision: date) -> date:
    """FOMC minutes are published three weeks after the decision day (Wednesday, 14:00 ET)."""
    return decision + timedelta(days=21)


def beige_book_date(decision: date) -> date:
    """Beige Book comes out two weeks before each FOMC meeting (Wednesday, 14:00 ET)."""
    return decision - timedelta(days=14)


# --- Israel CBS ------------------------------------------------------------


@dataclass(frozen=True)
class IsraelCalendar:
    holidays: frozenset[date]  # days the CBS does not publish (holidays)
    eves: frozenset[date]  # holiday eves (short days)

    def eligible(self, d: date) -> bool:
        return d.weekday() not in (4, 5) and d not in self.holidays and d not in self.eves


def cbs_release_ts(nominal: date, cal: IsraelCalendar, price_index: bool = True) -> datetime:
    """Price indices (CPI/PPI/housing): the 15th at 18:30 IL; if the 15th is Fri/Sat/holiday(-eve) -> the preceding eligible day at 14:00.
    Other releases: 13:00 IL on the first eligible day at or before the nominal date."""
    d = nominal
    if price_index:
        if cal.eligible(d):
            return local_ts(d, "18:30", "Asia/Jerusalem")
        while not cal.eligible(d):
            d -= timedelta(days=1)
        return local_ts(d, "14:00", "Asia/Jerusalem")
    while not cal.eligible(d):
        d -= timedelta(days=1)
    return local_ts(d, "13:00", "Asia/Jerusalem")


def tase_reporting_deadlines(year: int) -> list[tuple[date, str, str]]:
    """TASE statutory deadlines: annual report by Mar 31; quarterlies within 60 days of quarter end (May 31, Aug 31, Nov 30)."""
    return [
        (date(year, 3, 31), "FY", f"annual report FY{year - 1}"),
        (date(year, 5, 31), "Q1", f"Q1 {year} report"),
        (date(year, 8, 31), "Q2", f"Q2 {year} report"),
        (date(year, 11, 30), "Q3", f"Q3 {year} report"),
    ]


def month_range(start: date, end: date) -> list[tuple[int, int]]:
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def quarter_range(start: date, end: date) -> list[tuple[int, int]]:
    """(year, quarter) tuples covering [start, end]."""
    out = []
    y, q = start.year, (start.month - 1) // 3 + 1
    while (y, q) <= (end.year, (end.month - 1) // 3 + 1):
        out.append((y, q))
        q += 1
        if q > 4:
            y, q = y + 1, 1
    return out
