"""Calendar jobs. Suggested triggers (register in scheduler.py):
- calendar_official_daily_job    CronTrigger(hour=2, minute=0)                       (02:00 IL)
- calendar_vendor_hourly_job     IntervalTrigger(hours=1)
- calendar_actuals_job           CronTrigger(minute='*/15', hour='15-23,0-3')        (08:00–20:00 ET expressed in IL time)
- calendar_corporate_nightly_job CronTrigger(hour=1, minute=40)
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.calendar import service


async def calendar_official_daily_job() -> dict:
    today = date.today()
    return await service.refresh("official", today - timedelta(days=7), today + timedelta(days=120))


async def calendar_vendor_hourly_job() -> dict:
    today = date.today()
    return await service.refresh("vendor", today - timedelta(days=3), today + timedelta(days=14))


async def calendar_actuals_job() -> dict | str:
    """Every 15 min during 08:00–20:00 ET: fill actuals for releases in the last 3 hours (FMP if keyed, else ALFRED)."""
    now_et = datetime.now(UTC).astimezone(ZoneInfo("America/New_York"))
    if not (8 <= now_et.hour < 20):
        return "outside 08:00-20:00 ET"
    lo = (now_et - timedelta(hours=3)).date()
    return await service.refresh("actuals", lo, now_et.date())


async def calendar_corporate_nightly_job() -> dict:
    today = date.today()
    return await service.refresh("corporate", today - timedelta(days=2), today + timedelta(days=60))
