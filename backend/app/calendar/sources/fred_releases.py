"""FRED releases calendar (official schedule) + ALFRED first-print actuals for released events."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import polars as pl

from app.analytics.transforms import transform
from app.calendar.dictionary import Indicator, load_dictionary, period_from_rule
from app.calendar.rules import local_ts
from app.calendar.types import NormalizedEvent
from app.data.cache import make_key
from app.data.catalog.loader import get_spec, list_specs
from app.data.registry import get_registry

log = logging.getLogger(__name__)
RELEASE_SERIES_TTL = 7 * 24 * 3600
FRED_RELEASE_URL = "https://fred.stlouisfed.org/release?rid={rid}"


def first_print(df: pl.DataFrame, kind: str, freq: str) -> tuple[float | None, float | None, str]:
    """Apply the headline transform to a vintage frame; return (last, previous, reference_period)."""
    if df.is_empty():
        return None, None, ""
    out = transform(df.sort("ts"), kind, freq) if kind != "level" else df.sort("ts")
    if out.is_empty():
        return None, None, ""
    last_ts = out["ts"][-1]
    last = float(out["value"][-1])
    prev = float(out["value"][-2]) if out.height > 1 else None
    if freq == "1q":
        period = f"{last_ts.year}Q{(last_ts.month - 1) // 3 + 1}"
    elif freq == "1w":
        period = last_ts.date().isoformat()
    else:
        period = f"{last_ts.year}-{last_ts.month:02d}"
    return last, prev, period


@dataclass
class FredReleasesSource:
    id: str = "fred_releases"
    tier: str = "official"

    async def _release_series(self, rid: int, catalog_ids: set[str]) -> list[str]:
        from app.state import get_cache

        cache = get_cache()
        key = make_key("fred", "release_series", {"rid": rid})
        hit = await cache.get(key)
        if hit is not None:
            return [s for s in hit if s in catalog_ids]
        reg = get_registry()
        fred = reg.get("fred")
        data = await reg.call(
            fred,
            "release_series",
            lambda: fred._get("release/series", release_id=rid, limit=1000),
            key=str(rid),
        )
        ids = [f"fred:{s['id']}" for s in data.get("seriess", []) if s.get("id")]
        await cache.put(key, ids, RELEASE_SERIES_TTL, provider="fred", op="release_series")
        return [s for s in ids if s in catalog_ids]

    async def _alfred_actual(
        self, ind: Indicator, release_day: date
    ) -> tuple[float | None, float | None, str]:
        if not ind.headline_series or not ind.headline_series.startswith("fred:"):
            return None, None, ""
        spec = await get_spec(ind.headline_series)
        if spec is None:
            return None, None, ""
        reg = get_registry()
        fred = reg.get("fred")
        since = release_day - timedelta(days=800 if spec.freq == "1q" else 420)
        df = await reg.call(
            fred,
            "get_series_vintage",
            lambda: fred.get_series(spec, since=since, vintage=release_day),
            key=f"{spec.series_id}@{release_day}",
        )
        last, prev, period = first_print(df, ind.headline_transform, spec.freq)
        k = ind.headline_scale
        return (last * k if last is not None else None), (prev * k if prev is not None else None), period

    async def fetch(self, start: date, end: date, with_actuals: bool = True) -> list[NormalizedEvent]:
        reg = get_registry()
        try:
            fred = reg.get("fred")
        except Exception:
            return []
        if not reg.is_usable(fred)[0]:
            log.info("fred_releases: FRED not configured; skipping")
            return []
        dic = load_dictionary()
        catalog_ids = {sp.series_id for sp in await list_specs(provider="fred")}
        dates = await reg.call(
            fred, "release_dates", lambda: fred.release_dates(start, end), key=f"{start}..{end}"
        )
        today = date.today()
        out: list[NormalizedEvent] = []
        seen: set[tuple[str, str]] = set()
        for rd in dates:
            name = rd.get("release_name") or ""
            ind = dic.by_release(name, "US")
            if ind is None:
                continue
            try:
                d = date.fromisoformat(rd["date"])
            except (KeyError, ValueError):
                continue
            rid = int(rd.get("release_id") or 0)
            linked = list(ind.linked_series)
            if rid:
                try:
                    linked = sorted(set(linked) | set(await self._release_series(rid, catalog_ids)))
                except Exception as e:
                    log.debug("release/series %s failed: %s", rid, e)
            period = period_from_rule(ind.period_rule, d)
            actual = prev = None
            actual_src = None
            if with_actuals and d <= today:
                try:
                    actual, prev, p2 = await self._alfred_actual(ind, d)
                    if p2:
                        period = p2
                    if actual is not None:
                        actual_src = "alfred"
                except Exception as e:
                    log.debug("alfred actual for %s@%s failed: %s", ind.event_key, d, e)
            k = (ind.event_key, period or d.isoformat())
            if k in seen:
                continue
            seen.add(k)
            out.append(
                NormalizedEvent(
                    event_key=ind.event_key,
                    kind=ind.kind,
                    country="US",
                    currency="USD",
                    title=ind.title,
                    ts=local_ts(d, ind.release_time or "08:30", "America/New_York"),
                    importance=ind.importance,
                    category=ind.category,
                    unit=ind.unit,
                    reference_period=period,
                    status="released" if actual is not None else "scheduled",
                    actual=actual,
                    actual_source=actual_src,
                    previous=prev,
                    source=self.id,
                    source_url=FRED_RELEASE_URL.format(rid=rid) if rid else None,
                    linked_series=linked,
                    tier=self.tier,
                    raw={"release_id": rid, "release_name": name},
                )
            )
        return out
