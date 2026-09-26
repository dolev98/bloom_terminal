"""Plain-language data freshness: is the *data itself* current for its release cadence?

This is different from `series_service.is_stale`, which answers "is a re-fetch due?" (based on fetch time).
Here we look at the date of the latest observation. States:

  ok        latest observation is within the normal publication window for its frequency
  late      the next observation should normally have been published by now
  failed    the last refresh attempt failed (older data may still exist)
  never     never loaded / no observations yet
  paused    the series is disabled
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.data.providers.base import SeriesSpec

# Normal maximum age of the latest observation's timestamp (FRED/SDMX stamp the *start* of the period).
# Deliberately generous so "late" means genuinely behind: covers weekends/holidays and weekly FX releases (1d),
# 3-month-average labour data and 2-month-lagged house-price indices (1mo), and national accounts (1q).
MAX_AGE: dict[str, timedelta] = {
    "1d": timedelta(days=10),
    "1w": timedelta(days=21),
    "1mo": timedelta(days=125),
    "1q": timedelta(days=280),
    "1y": timedelta(days=800),
}


def _as_dt(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None) if v.tzinfo else v
    try:
        d = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return d.astimezone(UTC).replace(tzinfo=None) if d.tzinfo else d


def freshness(spec: SeriesSpec, meta: dict | None, now: datetime | None = None) -> dict:
    """Return {"state", "expected_by"}; `expected_by` = when the next observation is overdue (naive UTC ISO)."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    m = meta or {}
    last_ts = _as_dt(m.get("last_ts"))
    n = m.get("n_obs") or 0
    max_age = MAX_AGE.get(spec.freq)
    expected_by = None
    if last_ts is not None and max_age is not None:
        expected_by = last_ts + max_age + timedelta(days=spec.publication_lag_days or 0)
    if not spec.enabled:
        state = "paused"
    elif m.get("last_status") == "error":
        state = "failed"
    elif not n or last_ts is None:
        state = "never"
    elif expected_by is not None and now > expected_by:
        state = "late"
    else:
        state = "ok"
    return {"state": state, "expected_by": expected_by.isoformat() if expected_by else None}


def last_value(series_id: str) -> float | None:
    """Latest stored value (cheap: series files are small)."""
    from app.data.series_service import read_series

    try:
        df = read_series(series_id)
    except Exception:
        return None
    if df.is_empty():
        return None
    v = df["value"][-1]
    return float(v) if v is not None else None
