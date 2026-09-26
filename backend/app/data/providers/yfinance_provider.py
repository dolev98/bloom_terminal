"""yfinance adapter — GREY source (Yahoo ToS: personal use, unofficial, 429-prone).

Used for non-US/indices/FX/futures/TASE OHLCV and as a cross-check. Never the sole basis for an alert.
All calls run in a thread (yfinance is sync) behind a strict rate limit and a shared session.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import polars as pl

from app.data.providers.base import (
    Capability,
    LicenseSpec,
    Provider,
    ProviderError,
    Quote,
    RateSpec,
    SeriesSpec,
    empty_observations,
)

log = logging.getLogger(__name__)
_currency_cache: dict[str, str] = {}
_URL_RE = re.compile(r"finance\.yahoo\.com/quote/([A-Za-z0-9^=.\-]+)")

OHLCV_SCHEMA = {
    "ts": pl.Datetime("us"),
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "adj_close": pl.Float64,
    "volume": pl.Float64,
}


def empty_ohlcv() -> pl.DataFrame:
    return pl.DataFrame(schema=OHLCV_SCHEMA)


@dataclass
class YFinanceProvider(Provider):
    id: str = "yf"
    name: str = "Yahoo Finance (yfinance, unofficial)"
    capabilities: Capability = Capability.OHLCV | Capability.SERIES | Capability.QUOTES
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_second=0.5, per_minute=20, concurrency=1))
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(
            grey=True,
            store_allowed=True,
            personal_use_only=True,
            note="Yahoo: personal use only; unofficial endpoints",
        )
    )

    def parse_url(self, url: str) -> str | None:
        from urllib.parse import unquote

        m = _URL_RE.search(unquote(url))  # browsers copy ^GSPC as %5EGSPC
        return m.group(1) if m else None

    async def describe(self, key: str) -> SeriesSpec:
        ticker, _, fld = key.partition(":")
        fld = fld or "close"
        kind, cat, country = _classify(ticker)
        name = ticker
        try:
            info = await asyncio.to_thread(_fast_info, ticker)
            name = info.get("name") or ticker
        except Exception:
            pass
        return SeriesSpec(
            series_id=f"yf:{ticker}:{fld}",
            provider="yf",
            provider_key=ticker,
            field_name=fld,
            name=f"{name} ({fld})",
            freq="1d",
            unit=_currency(ticker),
            value_kind=kind,
            default_transform="log_ret",
            country=country,
            category=cat,
            license_note="yfinance (grey): personal use, unofficial",
            plausible_min=0 if kind == "price" else None,
        )

    async def get_ohlcv(self, ticker: str, interval: str = "1d", since: date | None = None) -> pl.DataFrame:
        df = await asyncio.to_thread(_download, ticker, interval, since)
        if not df.is_empty() and await self._is_agorot(ticker):
            df = df.with_columns(
                [(pl.col(c) / 100.0).alias(c) for c in ("open", "high", "low", "close", "adj_close")]
            )
        return df

    async def _is_agorot(self, ticker: str) -> bool:
        t = ticker.upper()
        if not t.endswith(".TA"):
            return False
        if t not in _currency_cache:
            try:
                info = await asyncio.to_thread(_fast_info, t)
                _currency_cache[t] = (info.get("currency") or "").upper()
            except Exception:
                _currency_cache[t] = ""
        return _currency_cache[t] == "ILA"

    async def get_series(
        self, spec: SeriesSpec, since: date | None = None, vintage: date | None = None
    ) -> pl.DataFrame:
        fld = spec.field_name or "close"
        df = await self.get_ohlcv(spec.provider_key, "1d", since)
        if df.is_empty() or fld not in df.columns:
            return empty_observations()
        return df.select(pl.col("ts"), pl.col(fld).alias("value")).drop_nulls("value")

    async def get_quotes(self, tickers: list[str]) -> list[Quote]:
        out: list[Quote] = []
        for t in tickers:
            try:
                info = await asyncio.to_thread(_fast_info, t)
            except Exception as e:
                log.debug("yf quote %s failed: %s", t, e)
                continue
            last = info.get("last")
            if last is None:
                continue
            prev = info.get("prev_close")
            cur = (info.get("currency") or _currency(t)).upper()
            scale = 100.0 if cur == "ILA" else 1.0
            if cur == "ILA":
                cur = "ILS"
            out.append(
                Quote(
                    ticker=t,
                    ts=datetime.now(tz=UTC),
                    last=float(last) / scale,
                    prev_close=(prev / scale) if prev else None,
                    change_pct=((last / prev - 1) * 100) if prev else None,
                    currency=cur,
                    source="yf",
                )
            )
        return out

    async def health(self) -> dict:
        try:
            df = await self.get_ohlcv("^GSPC", "1d", date.today().replace(day=1))
            return {"ok": not df.is_empty(), "rows": df.height}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}


# Exchange session close (local time) + a 15-minute settle margin, by Yahoo exchange timezone. A daily bar dated
# "today" is only a finished session after this time; before it, it is a partial intraday snapshot.
_SESSION_CLOSE = {
    "America/New_York": time(16, 15),
    "Asia/Jerusalem": time(17, 45),
    "Europe/London": time(16, 45),
    "Europe/Berlin": time(17, 45),
    "Asia/Tokyo": time(15, 45),
}


def _is_round_the_clock(ticker: str) -> bool:
    t = ticker.upper()
    return t.endswith("=X") or t.endswith("-USD") or t.endswith("=F") or t == "DX-Y.NYB"


def normalize_history(pdf, ticker: str, interval: str, now: datetime | None = None) -> pl.DataFrame:
    """Turn a yfinance `Ticker.history` frame (tz-aware index in the exchange timezone) into our OHLCV schema.
    Daily bars are keyed by the exchange-local session date; the current, unfinished session is dropped so a
    partial intraday snapshot is never stored as a close (live prices come from quotes instead)."""
    import pandas as pd

    if pdf is None or pdf.empty:
        return empty_ohlcv()
    idx = pdf.index
    tz = str(idx.tz) if getattr(idx, "tz", None) is not None else "UTC"
    daily = interval in ("1d", "5d", "1wk", "1mo", "3mo")
    if daily:
        local_dates = [d.date() for d in (idx.tz_convert(tz) if idx.tz is not None else idx)]
        ts = [datetime(d.year, d.month, d.day) for d in local_dates]
    else:
        ts = [
            d.to_pydatetime().replace(tzinfo=None)
            for d in (idx.tz_convert("UTC") if idx.tz is not None else idx)
        ]
    close = pdf["Close"].astype(float).to_numpy()
    adj = pdf["Adj Close"].astype(float).to_numpy() if "Adj Close" in pdf.columns else close
    df = pl.DataFrame(
        {
            "ts": ts,
            "open": pdf["Open"].astype(float).to_numpy(),
            "high": pdf["High"].astype(float).to_numpy(),
            "low": pdf["Low"].astype(float).to_numpy(),
            "close": close,
            "adj_close": adj,
            "volume": pdf["Volume"].astype(float).to_numpy() if "Volume" in pdf.columns else [0.0] * len(ts),
        }
    ).with_columns(pl.col("ts").cast(pl.Datetime("us")))
    df = (
        df.drop_nulls(subset=["close"])
        .filter(pl.col("close").is_not_nan())
        .unique(subset=["ts"], keep="last")
        .sort("ts")
    )
    if daily and df.height:
        now_local = (now or datetime.now(tz=UTC)).astimezone(ZoneInfo(tz))
        today = datetime(now_local.year, now_local.month, now_local.day)
        close_t = _SESSION_CLOSE.get(tz)
        session_done = (
            (not _is_round_the_clock(ticker)) and close_t is not None and now_local.time() >= close_t
        )
        if not session_done:
            df = df.filter(pl.col("ts") < today)
    _ = pd  # pandas is the input type
    return df


def _download(ticker: str, interval: str, since: date | None) -> pl.DataFrame:
    import yfinance as yf

    kwargs = {"interval": interval, "auto_adjust": False, "actions": False}
    if since:
        kwargs["start"] = since.isoformat()
    else:
        kwargs["period"] = "max"
    try:
        # Ticker.history keeps the exchange timezone; yf.download was observed to drop sessions (Sep 22 2026 missing).
        pdf = yf.Ticker(ticker).history(**kwargs)
    except Exception as e:  # pragma: no cover - network
        raise ProviderError(f"yfinance {ticker}: {e}") from e
    return normalize_history(pdf, ticker, interval)


def _fast_info(ticker: str) -> dict:
    import yfinance as yf

    t = yf.Ticker(ticker)
    fi = t.fast_info
    out = {
        "last": _g(fi, "last_price"),
        "prev_close": _g(fi, "previous_close"),
        "currency": _g(fi, "currency"),
        "name": None,
    }
    return out


def _g(fi, key):
    try:
        v = fi[key]
        return None if v != v else v  # NaN guard
    except Exception:
        return None


def _classify(ticker: str) -> tuple[str, str, str | None]:
    t = ticker.upper()
    if t.endswith(".TA"):
        return "price", "equity", "IL"
    if t.endswith("=X"):
        return "price", "fx", None
    if t.endswith("=F"):
        return "price", "commodity", None
    if t.endswith("-USD"):
        return "price", "crypto", None
    if t.startswith("^"):
        return (
            "level_index",
            "equity",
            "US" if t in ("^GSPC", "^NDX", "^DJI", "^RUT", "^SOX", "^VIX", "^MOVE") else None,
        )
    return "price", "equity", "US"


def _currency(ticker: str) -> str:
    t = ticker.upper()
    if t.endswith(".TA"):
        return "ILS"
    if t.endswith(".L"):
        return "GBp"
    return "USD"
