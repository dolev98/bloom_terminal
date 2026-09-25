"""Macro job. Suggested trigger: CronTrigger(hour='16,19', minute=20) in IL time (~09:20 ET and 19:20 IL) -> macro_daily_job."""

from __future__ import annotations

from app.macro import service


async def macro_daily_job() -> list[dict]:
    await service.ensure_catalog()
    return await service.refresh_all()
