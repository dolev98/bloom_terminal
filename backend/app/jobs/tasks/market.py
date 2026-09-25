from __future__ import annotations

from app.analytics.market_hours import any_market_open
from app.api.routers.watchlists import all_tickers
from app.market import service

HOME_TICKERS = [
    # dashboard tiles (keep in sync with frontend/src/pages/Dashboard.tsx)
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "^VIX",
    "TA35.TA",
    "^TA125.TA",
    "ILS=X",
    "DX-Y.NYB",
    "EURUSD=X",
    "GC=F",
    "CL=F",
    "BTC-USD",
    "EEM",
    # used by correlation presets
    "TLT",
    "HYG",
    "^GSPC",
    "^NDX",
]


async def quotes_poll_job() -> str:
    """Every 60 s: refresh quotes for watchlist + home tiles while a market is open (or once every 15 min otherwise)."""
    tickers = sorted(set(await all_tickers()) | set(HOME_TICKERS))
    if not tickers:
        return "no tickers"
    if not any_market_open():
        from datetime import datetime

        if datetime.now().minute % 15 != 0:
            return ""
    qs = await service.fetch_quotes(tickers)
    return f"{len(qs)}/{len(tickers)} quotes"


async def ohlcv_eod_job() -> list[dict]:
    tickers = sorted(set(await all_tickers()) | set(HOME_TICKERS))
    return await service.refresh_many(tickers)
