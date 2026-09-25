"""Shared synthetic-data helpers for the correlation tests (offline; no network)."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest


def days(
    n: int, start: datetime = datetime(2022, 1, 3), business: bool = False, step_days: int = 1
) -> pl.Series:
    """`n` timestamps from `start` (calendar days, or weekdays only when business=True)."""
    out: list[datetime] = []
    d = start
    while len(out) < n:
        if not business or d.weekday() < 5:
            out.append(d)
        d += timedelta(days=step_days)
    return pl.Series("ts", out, dtype=pl.Datetime("us"))


def obs(ts: pl.Series, values) -> pl.DataFrame:
    return pl.DataFrame({"ts": ts, "value": pl.Series(np.asarray(values, dtype=float))})


def random_walk(rng: np.random.Generator, n: int, start: float = 100.0, vol: float = 1.0) -> np.ndarray:
    return start + np.cumsum(rng.standard_normal(n) * vol)


def ar1(rng: np.random.Generator, n: int, phi: float = 0.8) -> np.ndarray:
    x = np.zeros(n)
    e = rng.standard_normal(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + e[i]
    return x


def synthetic_market(rng: np.random.Generator, n: int = 700) -> dict[str, pl.DataFrame]:
    """Business-day SP500 / SPY / DGS10 / VIX frames with a planted structure: SPY ≈ SP500, DGS10 changes
    negatively related to equity returns, VIX inversely related to returns."""
    ts = days(
        n,
        start=datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(days=int(n * 1.45) + 30),
        business=True,
    )
    r = 0.01 * rng.standard_normal(n)
    sp = 4000.0 * np.exp(np.cumsum(r))
    spy = sp / 10.0 * (1 + 0.001 * rng.standard_normal(n))
    dgs10 = np.clip(3.0 + np.cumsum(-2.0 * r + 0.02 * rng.standard_normal(n)), 0.5, 8.0)
    vix = np.clip(20.0 * np.exp(np.cumsum(-4.0 * r + 0.03 * rng.standard_normal(n)) * 0.3), 9.0, 80.0)
    return {
        "fred:SP500": obs(ts, sp),
        "SPY": obs(ts, spy),
        "fred:DGS10": obs(ts, dgs10),
        "fred:VIXCLS": obs(ts, vix),
    }


def month_starts(n: int) -> pl.Series:
    """First-of-month timestamps for the last `n` months (ending last month)."""
    today = datetime.now()
    y, m = today.year, today.month
    out: list[datetime] = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(datetime(y, m, 1))
    return pl.Series("ts", sorted(out), dtype=pl.Datetime("us"))


def ohlcv_from_close(df: pl.DataFrame) -> pl.DataFrame:
    c = df["value"]
    return pl.DataFrame(
        {
            "ts": df["ts"],
            "open": c,
            "high": c * 1.01,
            "low": c * 0.99,
            "close": c,
            "adj_close": c,
            "volume": pl.Series([1e6] * df.height),
        }
    )


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(42)


@pytest.fixture
def corr_client(client):
    """The shared `client` runs app.main's app, where the integrator has not yet included this router: include it
    here and keep the frontend StaticFiles mount (catch-all '/') behind the API routes."""
    from starlette.routing import Mount

    from app.api.routers.correlation import router
    from app.main import app

    if not any(getattr(r, "path", "") == "/api/correlation/pair" for r in app.router.routes):
        app.include_router(router)
        for m in [r for r in app.router.routes if isinstance(r, Mount)]:
            app.router.routes.remove(m)
            app.router.routes.append(m)
    return client
