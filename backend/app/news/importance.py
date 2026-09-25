"""Importance 0-100 per cluster = weighted sum of: coverage breadth (log distinct publishers), source tier,
market reaction (|1-day return| z and volume z from stored OHLCV), filing-type weight, |sentiment|.
Weights come from prefs (`news_importance_weights`), defaults below. Components are logged per cluster."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date, datetime, timedelta

import polars as pl

from app.data.store.models import Pref
from app.data.store.sqlite import session_scope

DEFAULT_WEIGHTS: dict[str, float] = {"coverage": 25, "tier": 20, "market": 25, "filing": 20, "sentiment": 10}
DEFAULT_LLM_THRESHOLD = 60
MAJOR_OUTLETS = {
    "reuters",
    "bloomberg",
    "wall street journal",
    "wsj",
    "financial times",
    "ft",
    "cnbc",
    "associated press",
    "ap news",
    "marketwatch",
    "barron's",
    "barrons",
    "the new york times",
    "nytimes",
    "the economist",
    "yahoo finance",
    "seeking alpha",
    "themarker",
    "globes",
    "calcalist",
    "bizportal",
    "haaretz",
    "the information",
    "axios",
    "techcrunch",
    "the verge",
}
HIGH_8K_ITEMS = {"2.02", "1.01", "2.01", "5.02", "1.03", "4.02", "2.06", "3.01", "1.05"}
FILING_WEIGHTS = {
    "10-K": 0.8,
    "10-Q": 0.8,
    "20-F": 0.8,
    "6-K": 0.5,
    "SC 13D": 1.0,
    "SC 13D/A": 0.8,
    "SC 13G": 0.6,
    "SC 13G/A": 0.4,
    "DEF 14A": 0.3,
    "4": 0.4,
}


def source_tier(kind: str, publisher: str | None, source_id: str = "") -> float:
    if kind in ("filing", "wire"):
        return 1.0
    p = (publisher or "").strip().lower()
    if p in MAJOR_OUTLETS or any(p.startswith(m) for m in ("reuters", "bloomberg")):
        return 0.7
    if source_id in ("finnhub_news", "massive", "alphavantage") and p:
        return 0.55
    return 0.4


def filing_weight(form: str | None, items: list[str] | None, size: int | None = None) -> float:
    if not form:
        return 0.0
    if form.startswith("8-K"):
        its = set(items or [])
        if its & HIGH_8K_ITEMS:
            return 1.0
        if its & {"7.01", "8.01", "3.02", "5.03", "5.07"}:
            return 0.5
        return 0.4
    w = FILING_WEIGHTS.get(form, 0.3)
    if form == "4" and size:  # crude proxy: bigger XML = more transactions
        w = min(0.8, w + min(0.4, size / 100_000))
    return w


def coverage_score(n_publishers: int) -> float:
    return min(1.0, math.log1p(max(0, n_publishers)) / math.log1p(8))


def market_reaction(ticker: str, event_day: date, lookback: int = 60) -> dict:
    """|next-day return| z-score and volume z from stored daily bars around `event_day`. 0 when unavailable."""
    from app.market.service import read_ohlcv

    try:
        df, _ = read_ohlcv(
            ticker, "1d", event_day - timedelta(days=lookback * 2), event_day + timedelta(days=5)
        )
    except Exception:
        return {"score": 0.0, "available": False}
    if df.is_empty() or df.height < 20:
        return {"score": 0.0, "available": False}
    df = df.sort("ts").with_columns(
        (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret"),
    )
    ev = datetime(event_day.year, event_day.month, event_day.day)
    after = df.filter(pl.col("ts") >= ev)
    if after.is_empty():
        return {"score": 0.0, "available": False}
    row = after.row(0, named=True)
    before = df.filter(pl.col("ts") < ev).tail(lookback)
    if before.height < 10:
        return {"score": 0.0, "available": False}
    ret_sd = before["ret"].drop_nulls().std() or 0.0
    vol_mean = before["volume"].mean() or 0.0
    vol_sd = before["volume"].std() or 0.0
    ret = row.get("ret")
    ret_z = abs(ret) / ret_sd if ret is not None and ret_sd > 1e-9 else 0.0
    vol_z = ((row.get("volume") or 0.0) - vol_mean) / vol_sd if vol_sd > 1e-9 else 0.0
    score = min(1.0, (ret_z + max(0.0, vol_z)) / 6.0)
    return {
        "score": round(score, 3),
        "ret_z": round(ret_z, 2),
        "vol_z": round(vol_z, 2),
        "ret": ret,
        "available": True,
        "bar_ts": str(row["ts"]),
    }


def score_cluster(
    *,
    kind: str,
    item_sources: list[tuple[str, str, str | None]],  # (source_id, kind, publisher)
    n_publishers: int,
    tickers: list[str],
    first_seen: datetime | None,
    filing_form: str | None = None,
    filing_items: list[str] | None = None,
    filing_size: int | None = None,
    sentiment: float | None = None,
    weights: dict[str, float] | None = None,
    market_lookup: Callable[[str, date], dict] | None = None,
) -> tuple[int, dict]:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    total_w = sum(w.values()) or 1.0
    tier = max((source_tier(k, p, sid) for sid, k, p in item_sources), default=0.4)
    parts: dict = {
        "coverage": round(coverage_score(n_publishers), 3),
        "tier": round(tier, 3),
        "filing": round(
            filing_weight(filing_form, filing_items, filing_size) if kind == "filing" or filing_form else 0.0,
            3,
        ),
        "sentiment": round(min(1.0, abs(sentiment or 0.0)), 3),
    }
    mkt = {"score": 0.0, "available": False}
    if tickers and first_seen is not None:
        lookup = market_lookup or market_reaction
        best = mkt
        for t in tickers[:3]:
            try:
                r = lookup(t, first_seen.date())
            except Exception:
                continue
            if r.get("score", 0) > best.get("score", 0) or (r.get("available") and not best.get("available")):
                best = r
        mkt = best
    parts["market"] = round(float(mkt.get("score", 0.0)), 3)
    parts["market_detail"] = {k: v for k, v in mkt.items() if k != "score"}
    raw = sum(w[k] * parts[k] for k in ("coverage", "tier", "market", "filing", "sentiment"))
    importance = int(round(100 * raw / total_w))
    parts["weights"] = w
    return max(0, min(100, importance)), parts


async def load_weights() -> dict[str, float]:
    async with session_scope() as s:
        row = await s.get(Pref, "news_importance_weights")
    if row and isinstance(row.value, dict):
        return {k: float(v) for k, v in row.value.items() if k in DEFAULT_WEIGHTS}
    return dict(DEFAULT_WEIGHTS)


async def llm_threshold() -> int:
    async with session_scope() as s:
        row = await s.get(Pref, "news_llm_threshold")
    try:
        return int(row.value) if row and row.value is not None else DEFAULT_LLM_THRESHOLD
    except (TypeError, ValueError):
        return DEFAULT_LLM_THRESHOLD
