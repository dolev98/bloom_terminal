"""Fiscal calendar helpers shared by every ingestion path (pure functions)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import date, timedelta

# duration (months) -> inclusive day-count window; 52/53-week calendars fall inside these.
DURATION_DAYS: dict[int, tuple[int, int]] = {3: (75, 105), 6: (165, 200), 9: (255, 290), 12: (350, 380)}


def norm_month(d: date) -> tuple[int, int]:
    """(year, month) a period boundary belongs to; the first 4 days of a month count as the previous month
    (52/53-week years end on e.g. Jan 2)."""
    if d.day <= 4:
        prev = d.replace(day=1) - timedelta(days=1)
        return prev.year, prev.month
    return d.year, d.month


def fiscal_year_of(end: date, fye_month: int) -> int:
    y, m = norm_month(end)
    return y if m <= fye_month else y + 1


def quarter_of(end: date, fye_month: int) -> int:
    _, m = norm_month(end)
    return ((m - fye_month - 1) % 12) // 3 + 1


def half_of(end: date, fye_month: int) -> int:
    return 1 if quarter_of(end, fye_month) <= 2 else 2


def is_fye(end: date, fye_month: int) -> bool:
    return norm_month(end)[1] == fye_month


def months_between(start: date, end: date) -> int | None:
    """3 / 6 / 9 / 12 for standard reporting durations, else None."""
    days = (end - start).days + 1
    for months, (lo, hi) in DURATION_DAYS.items():
        if lo <= days <= hi:
            return months
    return None


def infer_fye_month(ends: Iterable[date]) -> int | None:
    c = Counter(norm_month(e)[1] for e in ends)
    return c.most_common(1)[0][0] if c else None


def approx_start(end: date, months: int) -> date:
    """First day of a period of `months` ending at `end`: the day after the same day-of-month `months` earlier
    (clamped to month length), e.g. 2024-06-30 / 6 -> 2024-01-01."""
    y, m = end.year, end.month - months
    while m <= 0:
        m += 12
        y -= 1
    if end == _month_end(end.year, end.month):
        prev_end = _month_end(y, m)
    else:
        prev_end = date(y, m, min(end.day, _month_end(y, m).day))
    return prev_end + timedelta(days=1)


def _month_end(y: int, m: int) -> date:
    nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return nxt - timedelta(days=1)


def period_label(period_type: str, fiscal_year: int | None, fiscal_period: str | None, end: date) -> str:
    if period_type == "FY":
        return f"FY{fiscal_year}" if fiscal_year else f"FY {end.isoformat()}"
    if period_type == "TTM":
        return f"TTM {end.isoformat()}"
    fy = f"{fiscal_year % 100:02d}" if fiscal_year else ""
    return f"{fiscal_period or period_type} FY{fy}".strip()
