from __future__ import annotations

import io
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.market import service, stream

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/quotes")
async def quotes(
    tickers: str = Query(..., description="comma-separated"), refresh: bool = False
) -> list[dict]:
    syms = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if refresh:
        await service.fetch_quotes(syms)
    cached = {q["ticker"]: q for q in service.cached_quotes(syms)}
    missing = [s for s in syms if s not in cached]
    if missing:
        await service.fetch_quotes(missing)
        cached = {q["ticker"]: q for q in service.cached_quotes(syms)}
    return [cached[s] for s in syms if s in cached]


@router.get("/ohlcv/{ticker}")
async def ohlcv(
    ticker: str,
    interval: str = "1d",
    start: date | None = None,
    end: date | None = None,
    indicators: str = "",
    adjusted: bool = False,
    limit: int = Query(5000, le=50000),
) -> dict:
    names = [n for n in indicators.split(",") if n.strip()]
    df, added = service.read_ohlcv(ticker, interval, start, end, names, adjusted)
    if df.height > limit:
        df = df.tail(limit)
    cols = {c: df[c].to_list() for c in df.columns if c != "ts"}
    return {
        "ticker": ticker.upper(),
        "interval": interval,
        "n": df.height,
        "ts": [t.isoformat() for t in df["ts"].to_list()],
        "indicators": added,
        **cols,
    }


@router.post("/ohlcv/{ticker}/refresh")
async def refresh(ticker: str, interval: str = "1d", full: bool = False) -> dict:
    return await service.refresh_ohlcv(ticker, interval, full)


@router.get("/compare")
async def compare(tickers: str, start: date | None = None, adjusted: bool = True) -> dict:
    syms = [t.strip() for t in tickers.split(",") if t.strip()]
    df = service.compare(syms, start, adjusted)
    if df.is_empty() or "ts" not in df.columns or df.height == 0:
        return {"n": 0, "ts": [], "series": {}}
    return {
        "n": df.height,
        "ts": [t.isoformat() for t in df["ts"].to_list()],
        "series": {c: df[c].to_list() for c in df.columns if c != "ts"},
    }


@router.get("/ohlcv/{ticker}/export.csv")
async def export_csv(ticker: str, interval: str = "1d", adjusted: bool = False):
    df, _ = service.read_ohlcv(ticker, interval, None, None, None, adjusted)
    if df.is_empty():
        raise HTTPException(404, "no data")
    buf = io.StringIO()
    df.write_csv(buf)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{ticker.upper()}_{interval}.csv"'},
    )


@router.get("/sparklines")
async def sparklines(tickers: str, n: int = 30) -> dict[str, list[float]]:
    """Last n closes per ticker (for tiles/watchlist sparklines)."""
    out: dict[str, list[float]] = {}
    for t in [x.strip().upper() for x in tickers.split(",") if x.strip()]:
        df, _ = service.read_ohlcv(t, "1d")
        if df.height:
            out[t] = [float(v) for v in df["close"].tail(n).to_list()]
    return out


@router.get("/search")
async def search(q: str, limit: int = 12) -> list[dict]:
    from app.market import names

    return await names.search(q, limit)


@router.get("/profile/{ticker}")
async def profile(ticker: str) -> dict:
    from app.market import names

    return await names.profile(ticker)


@router.get("/stream")
async def stream_status() -> dict:
    return stream.status()
