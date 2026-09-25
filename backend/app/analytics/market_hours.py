"""Simple market-hours helpers (US and TASE). TASE trading days are refined from data in a later milestone."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
IL = ZoneInfo("Asia/Jerusalem")


def us_market_open(now: datetime | None = None) -> bool:
    now = (now or datetime.now(tz=NY)).astimezone(NY)
    if now.weekday() >= 5:
        return False
    return time(9, 30) <= now.time() <= time(16, 0)


def tase_market_open(now: datetime | None = None) -> bool:
    """TASE regular session. Since January 2026 TASE trades Monday–Friday (verified from 2026 daily bars:
    Friday bars present, no Sunday bars). Friday is a short session (closing time approximate)."""
    now = (now or datetime.now(tz=IL)).astimezone(IL)
    wd = now.weekday()  # Mon=0 … Sun=6
    if wd >= 5:
        return False
    if wd == 4:
        return time(9, 59) <= now.time() <= time(14, 0)
    return time(9, 59) <= now.time() <= time(17, 25)


def any_market_open(now: datetime | None = None) -> bool:
    return us_market_open(now) or tase_market_open(now)
