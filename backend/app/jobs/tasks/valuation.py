"""Valuation jobs: nightly fair values for watchlist tickers (after EOD) and macro refresh (rf / ERP / CRP / industry)."""

from __future__ import annotations

import logging

from app.api.routers.watchlists import all_tickers
from app.market.service import is_us_symbol
from app.valuation import service

log = logging.getLogger(__name__)


async def valuation_daily_job() -> list[dict]:
    """After EOD (23:30 IL): build inputs + run base models with an `auto` set for every watchlist equity -> fair_value_daily."""
    out: list[dict] = []
    for t in await all_tickers():
        if not (is_us_symbol(t) or t.endswith(".TA")):
            continue  # indices / FX / futures have no fundamentals
        try:
            res = await service.run_valuation(t, with_sensitivity=False)
            fcff = res["results"].get("fcff", {})
            out.append(
                {
                    "ticker": t,
                    "status": "ok" if fcff.get("value_per_share") is not None else "error",
                    "value": fcff.get("value_per_share"),
                    "upside": fcff.get("upside"),
                    "errors": res.get("errors"),
                }
            )
        except Exception as e:
            log.warning("valuation %s failed: %s", t, e)
            out.append({"ticker": t, "status": "error", "error": f"{type(e).__name__}: {e}"[:200]})
    return out


async def valuation_macro_job() -> dict:
    """Daily 07:30 IL: refresh rf (Treasury XML), monthly ERP + Israel CRP (Damodaran); industry datasets monthly on the 2nd."""
    from datetime import UTC, datetime, timedelta

    from app.valuation.providers import damodaran, treasury

    out: dict = {}
    try:
        rf = await treasury.get_rf(max_age=timedelta(hours=1))
        out["rf"] = rf["value"] if rf else None
        out["rf_series"] = await treasury.refresh_rf_series()
    except Exception as e:
        out["rf_error"] = str(e)[:200]
    try:
        erp = await damodaran.get_erp(max_age=timedelta(days=1))
        out["erp"] = erp["value"] if erp else None
        crp = await damodaran.get_crp("Israel", max_age=timedelta(days=7))
        out["crp_israel"] = crp["value"] if crp else None
    except Exception as e:
        out["damodaran_error"] = str(e)[:200]
    if datetime.now(tz=UTC).day == 2:
        out["industry"] = await damodaran.refresh_industry_stats()
    return out
