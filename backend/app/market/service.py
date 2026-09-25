"""Market service: quotes (Finnhub licensed → yfinance grey fallback), OHLCV refresh/read with indicators, compare/rebase."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import polars as pl

from app.analytics import indicators as ind
from app.core.config import get_settings
from app.data.providers.base import Capability, Provider, Quote
from app.data.providers.yfinance_provider import empty_ohlcv
from app.data.registry import get_registry
from app.data.store.models import QuoteRow
from app.data.store.ohlcv import OhlcvStore
from app.data.store.sqlite import session_scope
from app.ws.hub import get_hub

log = logging.getLogger(__name__)

_ohlcv: OhlcvStore | None = None
_quotes: dict[str, Quote] = {}
# Last official session per ticker from Finnhub REST quotes: (session date in New York, last price, previous close).
# Streamed (websocket) trades carry no previous close, so their day change is computed against this reference.
_session_ref: dict[str, tuple[date, float, float | None]] = {}
NY = ZoneInfo("America/New_York")


def ny_date(ts: datetime) -> date:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(NY).date()


def prev_close_for_trade(ticker: str, trade_ts: datetime, fallback: float | None) -> float | None:
    """Previous close to use for a streamed trade: if the trade is on a later New York date than the last REST
    session, the reference session's last price IS the previous close (e.g. pre-market the next morning)."""
    ref = _session_ref.get(ticker.upper())
    if not ref:
        return fallback
    ref_day, ref_last, ref_pc = ref
    if ny_date(trade_ts) > ref_day:
        return ref_last
    return ref_pc if ref_pc is not None else fallback


QUOTE_STALE_S = 15 * 60


def get_ohlcv_store() -> OhlcvStore:
    global _ohlcv
    if _ohlcv is None:
        _ohlcv = OhlcvStore(get_settings().parquet_dir)
    return _ohlcv


def set_ohlcv_store(store: OhlcvStore | None) -> None:
    global _ohlcv
    _ohlcv = store


def is_us_symbol(ticker: str) -> bool:
    t = ticker.upper()
    return not (t.startswith("^") or "=" in t or "-USD" in t or "." in t)


def _quote_providers(ticker: str) -> list[Provider]:
    reg = get_registry()
    order = ["finnhub", "yf"] if is_us_symbol(ticker) else ["yf"]
    out = []
    for pid in order:
        try:
            p = reg.get(pid)
        except Exception:
            continue
        if Capability.QUOTES in p.capabilities and reg.is_usable(p)[0]:
            out.append(p)
    return out


async def fetch_quotes(tickers: list[str]) -> list[Quote]:
    """Fetch quotes via the licensed provider first; grey fallback; updates the in-memory cache + SQLite + WS."""
    reg = get_registry()
    remaining = [t.upper() for t in tickers]
    got: list[Quote] = []
    for pid in ("finnhub", "yf"):
        if not remaining:
            break
        try:
            p = reg.get(pid)
        except Exception:
            continue
        if not reg.is_usable(p)[0]:
            continue
        batch = [t for t in remaining if pid == "yf" or is_us_symbol(t)]
        if not batch:
            continue
        try:
            qs = await reg.call(
                p, "get_quotes", lambda p=p, b=batch: p.get_quotes(b), key=",".join(batch[:10])
            )
        except Exception as e:
            log.warning("quotes via %s failed: %s", pid, e)
            continue
        got.extend(qs)
        done = {q.ticker.upper() for q in qs}
        remaining = [t for t in remaining if t not in done]
    if got:
        await store_quotes(got)
    return got


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


async def store_quotes(qs: list[Quote]) -> None:
    fresh: list[Quote] = []
    for q in qs:
        t = q.ticker.upper()
        if q.source == "finnhub":
            _session_ref[t] = (ny_date(q.ts), q.last, q.prev_close)
        cur = _quotes.get(t)
        # never replace a newer quote with an older one (e.g. a REST poll returning yesterday's close
        # while pre-market trades are already streaming)
        if cur is not None and _aware(cur.ts) > _aware(q.ts):
            # keep the newer streamed trade, but re-derive its previous close from the fresh session reference
            if cur.source == "finnhub-ws":
                pc = prev_close_for_trade(t, _aware(cur.ts), cur.prev_close)
                if pc and pc != cur.prev_close:
                    cur = cur.model_copy(update={"prev_close": pc, "change_pct": (cur.last / pc - 1) * 100})
                    _quotes[t] = cur
                    fresh.append(cur)
            continue
        _quotes[t] = q
        fresh.append(q)
    qs = fresh
    if not qs:
        return
    async with session_scope() as s:
        for q in qs:
            row = await s.get(QuoteRow, q.ticker.upper())
            vals = {
                "ts": q.ts.replace(tzinfo=None),
                "last": q.last,
                "prev_close": q.prev_close,
                "change_pct": q.change_pct,
                "open": q.open,
                "high": q.high,
                "low": q.low,
                "volume": q.volume,
                "currency": q.currency,
                "source": q.source,
            }
            if row is None:
                s.add(QuoteRow(ticker=q.ticker.upper(), **vals))
            else:
                for k, v in vals.items():
                    setattr(row, k, v)
    await get_hub().broadcast("quotes", [q.model_dump() for q in qs])


def cached_quotes(tickers: list[str] | None = None) -> list[dict]:
    now = datetime.now(tz=UTC)
    out = []
    for t, q in _quotes.items():
        if tickers and t not in [x.upper() for x in tickers]:
            continue
        age = (
            (now - q.ts).total_seconds() if q.ts.tzinfo else (now.replace(tzinfo=None) - q.ts).total_seconds()
        )
        out.append({**q.model_dump(), "age_s": int(age), "stale": age > QUOTE_STALE_S})
    return out


async def load_quotes_from_db() -> int:
    from sqlalchemy import select

    async with session_scope() as s:
        rows = (await s.execute(select(QuoteRow))).scalars().all()
    for r in rows:
        _quotes[r.ticker] = Quote(
            ticker=r.ticker,
            ts=r.ts.replace(tzinfo=UTC),
            last=r.last,
            prev_close=r.prev_close,
            change_pct=r.change_pct,
            open=r.open,
            high=r.high,
            low=r.low,
            volume=r.volume,
            currency=r.currency,
            source=r.source,
        )
    return len(rows)


# --- OHLCV ---------------------------------------------------------------------
OVERLAP_DAYS = {"1d": 14, "1wk": 60, "1w": 60}


async def refresh_ohlcv(ticker: str, interval: str = "1d", full: bool = False) -> dict:
    reg = get_registry()
    store = get_ohlcv_store()
    ticker = ticker.upper()
    provider = None
    for pid in ("alpaca", "yf"):
        try:
            p = reg.get(pid)
        except Exception:
            continue
        if (
            Capability.OHLCV in p.capabilities
            and reg.is_usable(p)[0]
            and (pid != "alpaca" or is_us_symbol(ticker))
        ):
            provider = p
            break
    if provider is None:
        return {
            "ticker": ticker,
            "status": "error",
            "error": "no usable OHLCV provider (enable grey sources or add Alpaca keys)",
        }
    since = None
    if not full:
        last = store.last_ts(ticker, interval)
        if last is not None:
            since = (last - timedelta(days=OVERLAP_DAYS.get(interval, 14))).date()
    try:
        df = await reg.call(
            provider, "get_ohlcv", lambda: provider.get_ohlcv(ticker, interval, since), key=ticker
        )
        merged = store.write(ticker, df, interval)
        return {
            "ticker": ticker,
            "status": "ok",
            "rows": df.height,
            "total": merged.height,
            "source": provider.id,
            "last_ts": merged["ts"].max() if merged.height else None,
        }
    except Exception as e:
        return {"ticker": ticker, "status": "error", "error": f"{type(e).__name__}: {e}"[:300]}


def read_ohlcv(
    ticker: str,
    interval: str = "1d",
    start: date | None = None,
    end: date | None = None,
    indicators: list[str] | None = None,
    adjusted: bool = False,
) -> tuple[pl.DataFrame, list[str]]:
    df = get_ohlcv_store().read(ticker, interval)
    if df.is_empty():
        return empty_ohlcv(), []
    if adjusted and "adj_close" in df.columns:
        factor = pl.col("adj_close") / pl.col("close")
        df = df.with_columns(
            (pl.col("open") * factor).alias("open"),
            (pl.col("high") * factor).alias("high"),
            (pl.col("low") * factor).alias("low"),
            pl.col("adj_close").alias("close"),
        )
    added: list[str] = []
    if indicators:
        df, added = ind.apply(df, indicators)
    if start is not None:
        df = df.filter(pl.col("ts") >= datetime(start.year, start.month, start.day))
    if end is not None:
        df = df.filter(pl.col("ts") <= datetime(end.year, end.month, end.day, 23, 59))
    return df, added


def compare(tickers: list[str], start: date | None = None, adjusted: bool = True) -> pl.DataFrame:
    """Wide frame of rebased (=100 at first common date) closes."""
    frames = []
    for t in tickers:
        df, _ = read_ohlcv(t, "1d", start, None, None, adjusted)
        if df.is_empty():
            continue
        frames.append(df.select(pl.col("ts"), pl.col("close").alias(t.upper())))
    if not frames:
        return pl.DataFrame({"ts": []})
    wide = frames[0]
    for f in frames[1:]:
        wide = wide.join(f, on="ts", how="inner")
    wide = wide.sort("ts")
    cols = [c for c in wide.columns if c != "ts"]
    return wide.with_columns([ind.rebase(wide[c]).alias(c) for c in cols])


async def refresh_many(tickers: list[str], interval: str = "1d") -> list[dict]:
    out = []
    for t in tickers:
        out.append(await refresh_ohlcv(t, interval))
        await asyncio.sleep(0)
    return out
