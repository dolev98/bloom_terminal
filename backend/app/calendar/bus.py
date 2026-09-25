"""Calendar bus: runs sources, merges events by (event_key, reference_period) with per-field tier priority, upserts rows + value vintages."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.calendar.models import Event, EventValue
from app.calendar.types import NormalizedEvent, Source
from app.data.store.sqlite import session_scope

log = logging.getLogger(__name__)

# Per-field tier priority (first wins). Unlisted tiers rank last.
PRIORITY: dict[str, tuple[str, ...]] = {
    "ts": ("official", "rule", "vendor", "forexfactory", "user"),
    "end_ts": ("official", "rule", "vendor", "forexfactory", "user"),
    "title": ("official", "rule", "vendor", "forexfactory", "user"),
    "actual": ("official", "vendor", "forexfactory", "rule", "user"),
    "consensus": ("vendor", "forexfactory", "official", "rule", "user"),
    "forecast_alt": ("forexfactory", "vendor", "official", "rule", "user"),
    "previous": ("official", "vendor", "forexfactory", "rule", "user"),
    "previous_revised": ("official", "vendor", "forexfactory", "rule", "user"),
    "unit": ("official", "vendor", "forexfactory", "rule", "user"),
    "source_url": ("official", "rule", "vendor", "forexfactory", "user"),
    "notes": ("official", "rule", "vendor", "forexfactory", "user"),
    "status": ("official", "vendor", "forexfactory", "rule", "user"),
    "currency": ("official", "vendor", "forexfactory", "rule", "user"),
    "category": ("official", "rule", "vendor", "forexfactory", "user"),
    "kind": ("official", "rule", "vendor", "forexfactory", "user"),
}
_SOURCE_FIELDS = {"actual": "actual_source", "consensus": "consensus_source"}


def _rank(field: str, tier: str) -> int:
    order = PRIORITY.get(field, PRIORITY["title"])
    return order.index(tier) if tier in order else len(order)


def merge_group(group: list[NormalizedEvent]) -> NormalizedEvent:
    """Merge events describing the same release: each field taken from the highest-priority source that has it."""
    base = min(group, key=lambda e: _rank("title", e.tier)).model_copy(deep=True)
    for fld in PRIORITY:
        ranked = sorted(group, key=lambda e: _rank(fld, e.tier))
        for e in ranked:
            v = getattr(e, fld, None)
            if v not in (None, ""):
                setattr(base, fld, v)
                src_fld = _SOURCE_FIELDS.get(fld)
                if src_fld:
                    setattr(base, src_fld, getattr(e, src_fld, None) or e.source)
                break
    base.importance = max(e.importance for e in group)
    base.linked_series = sorted({s for e in group for s in e.linked_series})
    base.affected_tickers = sorted({t for e in group for t in e.affected_tickers})
    base.tags = sorted({t for e in group for t in e.tags})
    contributors = sorted({e.source for e in group})
    base.raw = {**base.raw, "sources": contributors}
    if base.actual is not None and base.status == "scheduled":
        base.status = "released"
    return base


def merge_events(events: Iterable[NormalizedEvent]) -> list[NormalizedEvent]:
    """Group by (event_key, reference_period); events without a period attach to a same-key event within ±1 day."""
    with_period: dict[tuple[str, str], list[NormalizedEvent]] = {}
    without: list[NormalizedEvent] = []
    for e in events:
        if e.reference_period:
            with_period.setdefault((e.event_key, e.reference_period), []).append(e)
        else:
            without.append(e)
    by_key_date: dict[tuple[str, date], list[NormalizedEvent]] = {}
    for e in without:
        attached = False
        for delta in (0, -1, 1):
            d = e.ts.date() + timedelta(days=delta)
            for (k, _p), grp in with_period.items():
                if k == e.event_key and any(g.ts.date() == d for g in grp):
                    grp.append(e)
                    attached = True
                    break
            if attached:
                break
        if not attached:
            by_key_date.setdefault((e.event_key, e.ts.date()), []).append(e)
    out = [merge_group(g) for g in with_period.values()]
    out.extend(merge_group(g) for g in by_key_date.values())
    return sorted(out, key=lambda e: (e.ts, e.country, e.event_key))


async def _find_existing(s, e: NormalizedEvent) -> Event | None:
    if e.reference_period:
        row = (
            (
                await s.execute(
                    select(Event).where(
                        Event.event_key == e.event_key, Event.reference_period == e.reference_period
                    )
                )
            )
            .scalars()
            .first()
        )
        if row:
            return row
    lo, hi = e.ts.date() - timedelta(days=1), e.ts.date() + timedelta(days=1)
    rows = (
        (
            await s.execute(
                select(Event).where(
                    Event.event_key == e.event_key, Event.release_date >= lo, Event.release_date <= hi
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    # prefer the one without a period (or the same period), closest in time
    rows = [
        r
        for r in rows
        if not e.reference_period or not r.reference_period or r.reference_period == e.reference_period
    ]
    return min(rows, key=lambda r: abs((r.release_ts - e.ts).total_seconds())) if rows else None


def _latest_value(row: Event) -> EventValue | None:
    return row.values[-1] if row.values else None


def _changed(cur: EventValue | None, e: NormalizedEvent) -> bool:
    if cur is None:
        return any(
            v is not None for v in (e.consensus, e.previous, e.actual, e.forecast_alt, e.previous_revised)
        )
    for fld in ("consensus", "previous", "actual", "forecast_alt", "previous_revised"):
        nv = getattr(e, fld)
        if nv is not None and nv != getattr(cur, fld):
            return True
    return False


async def upsert_events(events: list[NormalizedEvent]) -> dict:
    """Insert or update rows; append a value vintage when numbers change. Returns counters + touched event ids."""
    inserted = updated = vintages = 0
    touched: list[int] = []
    keys_with_actual: set[str] = set()
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_scope() as s:
        for e in events:
            row = await _find_existing(s, e)
            if row is None:
                row = Event(
                    event_key=e.event_key,
                    kind=e.kind,
                    country=e.country,
                    currency=e.currency,
                    title=e.title,
                    category=e.category or "other",
                    importance=e.importance,
                    release_ts=e.ts,
                    release_date=e.ts.date(),
                    end_ts=e.end_ts,
                    reference_period=e.reference_period,
                    status=e.status,
                    source_provider=e.source,
                    source_url=e.source_url,
                    linked_series=list(e.linked_series),
                    affected_tickers=list(e.affected_tickers),
                    notes=e.notes,
                )
                s.add(row)
                await s.flush()
                inserted += 1
            else:
                if row.source_provider == "user" and e.source != "user":
                    continue  # never let feeds overwrite user rows
                row.release_ts = e.ts
                row.release_date = e.ts.date()
                if e.end_ts is not None:
                    row.end_ts = e.end_ts
                if e.reference_period and not row.reference_period:
                    row.reference_period = e.reference_period
                row.title = e.title or row.title
                row.kind = e.kind or row.kind
                row.category = e.category or row.category
                row.importance = max(row.importance, e.importance)
                row.currency = e.currency or row.currency
                row.source_url = e.source_url or row.source_url
                row.linked_series = sorted(set(row.linked_series or []) | set(e.linked_series))
                row.affected_tickers = sorted(set(row.affected_tickers or []) | set(e.affected_tickers))
                if e.notes:
                    row.notes = e.notes
                if e.source and e.source not in (row.source_provider or ""):
                    row.source_provider = ",".join(
                        sorted((set((row.source_provider or "").split(",")) | {e.source}) - {""})
                    )
                row.ingest_version = (row.ingest_version or 1) + 1
                updated += 1
            await s.refresh(row, attribute_names=["values"])
            cur = _latest_value(row)
            if cur is not None and not cur.unit and e.unit:
                cur.unit = e.unit  # fill a unit an earlier (unit-less) parse left empty; not a new vintage
            if _changed(cur, e):
                actual = e.actual if e.actual is not None else (cur.actual if cur else None)
                consensus = e.consensus if e.consensus is not None else (cur.consensus if cur else None)
                surprise = (actual - consensus) if actual is not None and consensus is not None else None
                spct = (surprise / abs(consensus) * 100.0) if surprise is not None and consensus else None
                if cur is not None and cur.actual is not None and actual is not None and actual != cur.actual:
                    row.status = "revised"
                elif actual is not None:
                    row.status = "released"
                s.add(
                    EventValue(
                        event_id=row.id,
                        vintage_ts=now,
                        consensus=consensus,
                        consensus_source=e.consensus_source or (cur.consensus_source if cur else None),
                        forecast_alt=e.forecast_alt
                        if e.forecast_alt is not None
                        else (cur.forecast_alt if cur else None),
                        previous=e.previous if e.previous is not None else (cur.previous if cur else None),
                        previous_revised=e.previous_revised
                        if e.previous_revised is not None
                        else (cur.previous_revised if cur else None),
                        actual=actual,
                        actual_source=e.actual_source or (cur.actual_source if cur else None),
                        unit=e.unit or (cur.unit if cur else None),
                        surprise=surprise,
                        surprise_pct=spct,
                        surprise_z=None,
                    )
                )
                vintages += 1
                if actual is not None:
                    keys_with_actual.add(row.event_key)
            elif e.actual is None and row.status == "scheduled" and e.status != row.status:
                row.status = e.status
            touched.append(row.id)
    return {
        "inserted": inserted,
        "updated": updated,
        "vintages": vintages,
        "ids": touched,
        "keys": sorted(keys_with_actual),
    }


class CalendarBus:
    def __init__(self, sources: list[Source] | None = None):
        self.sources: list[Source] = list(sources or [])

    def register(self, source: Source) -> None:
        self.sources = [s for s in self.sources if s.id != source.id] + [source]

    async def collect(
        self, start: date, end: date, only: set[str] | None = None
    ) -> tuple[list[NormalizedEvent], dict[str, str]]:
        raw: list[NormalizedEvent] = []
        errors: dict[str, str] = {}
        for src in self.sources:
            if only and src.id not in only:
                continue
            try:
                evs = await src.fetch(start, end)
                for e in evs:
                    e.tier = e.tier or src.tier
                raw.extend(evs)
                log.info("calendar source %s: %d events", src.id, len(evs))
            except Exception as e:  # one broken feed must not sink the others
                errors[src.id] = f"{type(e).__name__}: {e}"[:300]
                log.warning("calendar source %s failed: %s", src.id, errors[src.id])
        return raw, errors

    async def run(self, start: date, end: date, only: set[str] | None = None) -> dict:
        raw, errors = await self.collect(start, end, only)
        merged = merge_events(raw)
        res = await upsert_events(merged)
        if res["keys"]:
            from app.calendar import surprise

            await surprise.recompute_keys(res["keys"])
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "fetched": len(raw),
            "merged": len(merged),
            "errors": errors,
            **{k: v for k, v in res.items() if k != "ids"},
        }


def default_sources() -> list[Source]:
    from app.calendar.sources import build_sources

    return build_sources()


_bus: CalendarBus | None = None


def get_bus() -> CalendarBus:
    global _bus
    if _bus is None:
        _bus = CalendarBus(default_sources())
    return _bus


def set_bus(bus: CalendarBus | None) -> None:
    global _bus
    _bus = bus
