"""Monthly USD budget for Claude API usage. Every LLM call records usage; calls are refused once the budget is spent."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.config import get_settings
from app.data.store.sqlite import session_scope
from app.llm.models import LlmUsage

# $ per million tokens: (input, output). Cache read = 0.1x input, cache write = 1.25x input.
PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
}


class BudgetExceeded(Exception):
    pass


def cost_usd(
    model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0
) -> float:
    pin, pout = PRICES.get(model, (5.0, 25.0))
    return (
        input_tokens * pin + output_tokens * pout + cache_read * pin * 0.1 + cache_write * pin * 1.25
    ) / 1e6


def _month_start() -> datetime:
    now = datetime.now(tz=UTC)
    return datetime(now.year, now.month, 1)


async def monthly_spent() -> float:
    async with session_scope() as s:
        v = (
            await s.execute(
                select(func.coalesce(func.sum(LlmUsage.cost_usd), 0.0)).where(LlmUsage.ts >= _month_start())
            )
        ).scalar_one()
    return float(v or 0.0)


async def check(estimated_usd: float = 0.0) -> None:
    limit = get_settings().llm_monthly_budget_usd
    spent = await monthly_spent()
    if spent + estimated_usd > limit:
        raise BudgetExceeded(
            f"LLM monthly budget exhausted: spent ${spent:.2f} + est ${estimated_usd:.2f} > ${limit:.2f}"
        )


async def record(
    model: str,
    purpose: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_write: int = 0,
    meta: dict | None = None,
) -> float:
    c = cost_usd(model, input_tokens, output_tokens, cache_read, cache_write)
    async with session_scope() as s:
        s.add(
            LlmUsage(
                model=model,
                purpose=purpose,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                cost_usd=c,
                meta=meta or {},
            )
        )
    return c


async def summary() -> dict:
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(LlmUsage.purpose, LlmUsage.model, func.count(), func.sum(LlmUsage.cost_usd))
                .where(LlmUsage.ts >= _month_start())
                .group_by(LlmUsage.purpose, LlmUsage.model)
            )
        ).all()
    return {
        "month_spent_usd": round(sum(r[3] or 0 for r in rows), 4),
        "budget_usd": get_settings().llm_monthly_budget_usd,
        "by_purpose": [
            {"purpose": r[0], "model": r[1], "calls": r[2], "cost_usd": round(r[3] or 0, 4)} for r in rows
        ],
    }
