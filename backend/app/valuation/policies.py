"""Valuation policy switches (prefs.valuation_policies): SBC as cash expense, leases as debt, R&D capitalization, mid-year, g cap."""

from __future__ import annotations

from pydantic import BaseModel

from app.data.store.models import Pref
from app.data.store.sqlite import session_scope

PREF_KEY = "valuation_policies"


class Policies(BaseModel):
    sbc_cash_expense: bool = True  # subtract stock-based comp from FCFF
    leases_as_debt: bool = True  # long_term_lease_liabilities in the equity bridge (and WACC weights)
    rd_capitalize: bool = False  # capitalize R&D (5y straight line) when `research_development` exists
    mid_year: bool = False  # mid-year discounting convention
    g_cap: float = 0.03  # terminal growth cap (also capped at rf)
    forecast_years: int = 5


DEFAULT_POLICIES = Policies()


async def get_policies() -> Policies:
    async with session_scope() as s:
        row = await s.get(Pref, PREF_KEY)
    if row is None or not isinstance(row.value, dict):
        return Policies()
    return Policies(**{**Policies().model_dump(), **row.value})


async def put_policies(update: dict) -> Policies:
    current = await get_policies()
    merged = Policies(**{**current.model_dump(), **update})
    async with session_scope() as s:
        row = await s.get(Pref, PREF_KEY)
        if row is None:
            s.add(Pref(key=PREF_KEY, value=merged.model_dump()))
        else:
            row.value = merged.model_dump()
    return merged
