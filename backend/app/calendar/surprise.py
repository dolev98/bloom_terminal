"""Surprise analytics: surprise z-scores per event key, per-country economic surprise index (ESI), 1-day ticker reactions."""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta

import polars as pl
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.calendar.models import Event, EventValue
from app.data.catalog.loader import get_spec, upsert_spec
from app.data.providers.base import SeriesSpec
from app.data.series_service import write_manual
from app.data.store.sqlite import session_scope

log = logging.getLogger(__name__)
WINDOW = 24
MIN_HISTORY = 4
ESI_WINDOW_DAYS = 90


def surprise_of(actual: float | None, consensus: float | None) -> tuple[float | None, float | None]:
    if actual is None or consensus is None:
        return None, None
    s = actual - consensus
    pct = (s / abs(consensus) * 100.0) if consensus else None
    return s, pct


def rolling_z(
    surprises: list[float], window: int = WINDOW, min_history: int = MIN_HISTORY
) -> list[float | None]:
    """z_i = s_i / std(s_{i-window+1..i}) — the current surprise scaled by the dispersion of the last `window` surprises."""
    out: list[float | None] = []
    for i, s in enumerate(surprises):
        hist = surprises[max(0, i - window + 1) : i + 1]
        if len(hist) < min_history:
            out.append(None)
            continue
        mean = sum(hist) / len(hist)
        var = sum((x - mean) ** 2 for x in hist) / (len(hist) - 1)
        sd = math.sqrt(var)
        out.append(None if sd == 0 else max(-6.0, min(6.0, s / sd)))
    return out


async def recompute_keys(keys: list[str]) -> int:
    """Recompute surprise/surprise_z on the latest vintage of every released event for these keys."""
    n = 0
    async with session_scope() as s:
        for key in keys:
            rows = (
                (
                    await s.execute(
                        select(Event)
                        .where(Event.event_key == key)
                        .options(selectinload(Event.values))
                        .order_by(Event.release_ts)
                    )
                )
                .scalars()
                .all()
            )
            seq: list[tuple[EventValue, float]] = []
            for r in rows:
                if not r.values:
                    continue
                v = r.values[-1]
                sp, pct = surprise_of(v.actual, v.consensus)
                if sp is None:
                    continue
                v.surprise, v.surprise_pct = sp, pct
                seq.append((v, sp))
            zs = rolling_z([sp for _, sp in seq])
            for (v, _), z in zip(seq, zs, strict=True):
                v.surprise_z = z
                n += 1
    return n


async def surprise_history(event_key: str, limit: int = WINDOW) -> list[dict]:
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(Event)
                    .where(Event.event_key == event_key, Event.status.in_(("released", "revised")))
                    .options(selectinload(Event.values))
                    .order_by(Event.release_ts.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    out = []
    for r in reversed(rows):
        v = r.values[-1] if r.values else None
        if v is None:
            continue
        out.append(
            {
                "event_id": r.id,
                "release_ts": r.release_ts,
                "reference_period": r.reference_period,
                "consensus": v.consensus,
                "actual": v.actual,
                "previous": v.previous,
                "surprise": v.surprise,
                "surprise_pct": v.surprise_pct,
                "surprise_z": v.surprise_z,
            }
        )
    return out


# --- Economic surprise index ---------------------------------------------------


def esi_from_points(
    points: list[tuple[date, float, int]], window_days: int = ESI_WINDOW_DAYS
) -> pl.DataFrame:
    """points = (release_date, surprise_z, importance). ESI(d) = importance-weighted mean of z over (d - window, d]."""
    if not points:
        return pl.DataFrame(schema={"ts": pl.Datetime("us"), "value": pl.Float64})
    pts = sorted(points, key=lambda p: p[0])
    days = sorted({p[0] for p in pts})
    ts, vals = [], []
    for d in days:
        lo = d - timedelta(days=window_days)
        num = den = 0.0
        for pd, z, w in pts:
            if lo < pd <= d:
                num += w * z
                den += w
        if den:
            ts.append(datetime(d.year, d.month, d.day))
            vals.append(num / den)
    return pl.DataFrame({"ts": ts, "value": vals}).with_columns(pl.col("ts").cast(pl.Datetime("us")))


async def build_esi(country: str) -> dict:
    """Write derived:ESI_<CC> from every released macro/cb event of the country with a surprise z."""
    cc = country.upper()
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(Event)
                    .where(
                        Event.country == cc,
                        Event.kind.in_(("macro", "cb_decision")),
                        Event.status.in_(("released", "revised")),
                    )
                    .options(selectinload(Event.values))
                )
            )
            .scalars()
            .all()
        )
    points = []
    for r in rows:
        v = r.values[-1] if r.values else None
        if v is None or v.surprise_z is None:
            continue
        points.append((r.release_date, float(v.surprise_z), int(r.importance or 1)))
    df = esi_from_points(points)
    sid = f"derived:ESI_{cc}"
    if await get_spec(sid) is None:
        await upsert_spec(
            SeriesSpec(
                series_id=sid,
                provider="derived",
                provider_key=f"ESI_{cc}",
                name=f"Economic surprise index {cc} (3m, importance-weighted z)",
                description="Computed by the calendar bus from released events (actual - consensus, scaled by the trailing-24 surprise std). Not refreshable from a provider.",
                freq="irregular",
                unit="z",
                value_kind="survey",
                default_transform="level",
                country=cc,
                category="sentiment",
                tags=["esi", "surprise", "computed"],
                enabled=False,
            )
        )
    if df.is_empty():
        return {"series_id": sid, "n": 0}
    write_manual(sid, df, replace=True)
    from app.data.series_service import _update_meta

    await _update_meta(sid, "ok", None, df.height)
    return {"series_id": sid, "n": df.height, "last": df["value"][-1]}


# --- Ticker reactions ------------------------------------------------------------


def reaction_1d(ticker: str, release_ts: datetime) -> dict | None:
    """Close-to-close return of the first session at/after the release (or the next one when released after 16:00 ET)."""
    from zoneinfo import ZoneInfo

    from app.market.service import read_ohlcv

    df, _ = read_ohlcv(
        ticker, "1d", start=release_ts.date() - timedelta(days=10), end=release_ts.date() + timedelta(days=6)
    )
    if df.is_empty() or df.height < 2:
        return None
    local = release_ts.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/New_York"))
    target = local.date() if local.hour < 16 else local.date() + timedelta(days=1)
    rows = df.sort("ts").to_dicts()
    for i, r in enumerate(rows):
        if r["ts"].date() >= target and i > 0:
            prev = rows[i - 1]["close"]
            if not prev:
                return None
            return {
                "ticker": ticker,
                "session": r["ts"].date().isoformat(),
                "ret_pct": (r["close"] / prev - 1.0) * 100.0,
            }
    return None
