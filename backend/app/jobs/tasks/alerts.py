"""Alert jobs: evaluate every 60 s (market hours) / every 15 min otherwise; digest flush at 09:00 IL."""

from __future__ import annotations

from datetime import datetime

from app.alerts import digest, engine
from app.analytics.market_hours import any_market_open


async def alerts_evaluate_job() -> str:
    """Scheduled every 60 s. Outside market hours only the :00/:15/:30/:45 ticks evaluate (cheap, idempotent)."""
    if not any_market_open() and datetime.now().minute % 15 != 0:
        return ""
    res = await engine.evaluate_all("job")
    return f"{res['evaluated']} evaluated, {res['fired']} fired"


async def alerts_digest_flush_job() -> dict:
    """09:00 IL: send everything queued during quiet hours as one message."""
    return await digest.flush()
