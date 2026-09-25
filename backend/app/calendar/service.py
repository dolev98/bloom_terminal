"""Calendar service: queries for the API (events, week strip, central-bank strip, ICS export), user events, refresh orchestration."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from icalendar import Calendar as ICal
from icalendar import Event as IEvent
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.calendar.dictionary import FLAGS, load_dictionary
from app.calendar.models import Event, EventValue
from app.calendar.sources.central_banks import next_decisions
from app.data.store.sqlite import session_scope

log = logging.getLogger(__name__)
IL = ZoneInfo("Asia/Jerusalem")
NY = ZoneInfo("America/New_York")


def _utc(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def _val(v: EventValue | None) -> dict:
    if v is None:
        return {
            "consensus": None,
            "actual": None,
            "previous": None,
            "surprise": None,
            "surprise_pct": None,
            "surprise_z": None,
            "unit": None,
        }
    return {
        "vintage_ts": v.vintage_ts,
        "consensus": v.consensus,
        "consensus_source": v.consensus_source,
        "forecast_alt": v.forecast_alt,
        "previous": v.previous,
        "previous_revised": v.previous_revised,
        "actual": v.actual,
        "actual_source": v.actual_source,
        "unit": v.unit,
        "surprise": v.surprise,
        "surprise_pct": v.surprise_pct,
        "surprise_z": v.surprise_z,
    }


def dump_event(r: Event) -> dict:
    v = r.values[-1] if r.values else None
    ts = _utc(r.release_ts)
    return {
        "id": r.id,
        "event_key": r.event_key,
        "kind": r.kind,
        "country": r.country,
        "flag": FLAGS.get(r.country, ""),
        "currency": r.currency,
        "title": r.title,
        "category": r.category,
        "importance": r.importance,
        "release_ts": r.release_ts,
        "release_ts_il": ts.astimezone(IL).isoformat() if ts else None,
        "release_ts_et": ts.astimezone(NY).isoformat() if ts else None,
        "end_ts": r.end_ts,
        "reference_period": r.reference_period,
        "status": r.status,
        "source_provider": r.source_provider,
        "source_url": r.source_url,
        "linked_series": r.linked_series or [],
        "affected_tickers": r.affected_tickers or [],
        "notes": r.notes,
        "values": _val(v),
    }


async def _watchlist_tickers() -> set[str]:
    try:
        from app.api.routers.watchlists import all_tickers

        return {t.upper() for t in await all_tickers()}
    except Exception:
        return set()


async def list_events(
    start: date,
    end: date,
    countries: list[str] | None = None,
    kinds: list[str] | None = None,
    min_importance: int = 1,
    watchlist_only: bool = False,
    q: str | None = None,
) -> list[dict]:
    stmt = (
        select(Event)
        .where(Event.release_date >= start, Event.release_date <= end, Event.importance >= min_importance)
        .options(selectinload(Event.values))
        .order_by(Event.release_ts, Event.country, Event.importance.desc())
    )
    if countries:
        stmt = stmt.where(Event.country.in_([c.upper() for c in countries]))
    if kinds:
        stmt = stmt.where(Event.kind.in_(kinds))
    async with session_scope() as s:
        rows = (await s.execute(stmt)).scalars().all()
    out = [dump_event(r) for r in rows]
    if watchlist_only:
        wl = await _watchlist_tickers()
        out = [e for e in out if not e["affected_tickers"] or set(e["affected_tickers"]) & wl]
    if q:
        ql = q.lower()
        out = [e for e in out if ql in e["title"].lower() or ql in e["event_key"]]
    return out


async def get_event(event_id: int) -> dict | None:
    from app.calendar.surprise import reaction_1d, surprise_history
    from app.data.catalog.loader import get_spec
    from app.data.series_service import read_series

    async with session_scope() as s:
        r = (
            (await s.execute(select(Event).where(Event.id == event_id).options(selectinload(Event.values))))
            .scalars()
            .first()
        )
        if r is None:
            return None
        out = dump_event(r)
        out["values_history"] = [_val(v) for v in r.values]
    out["past_surprises"] = await surprise_history(r.event_key, limit=24)
    linked = []
    for sid in r.linked_series or []:
        spec = await get_spec(sid)
        df = read_series(sid)
        tail = df.tail(24)
        linked.append(
            {
                "series_id": sid,
                "name": spec.name if spec else sid,
                "unit": spec.unit if spec else "",
                "last": float(df["value"][-1]) if df.height else None,
                "last_ts": df["ts"][-1] if df.height else None,
                "sparkline": {
                    "ts": [t.isoformat() for t in tail["ts"].to_list()],
                    "value": tail["value"].to_list(),
                },
            }
        )
    out["linked"] = linked
    reactions = []
    for t in (r.affected_tickers or [])[:10]:
        for past in out["past_surprises"][-8:]:
            rt = past["release_ts"]
            try:
                rr = reaction_1d(t, rt)
            except Exception:
                rr = None
            if rr:
                reactions.append({**rr, "event_id": past["event_id"], "surprise_z": past["surprise_z"]})
    out["reactions"] = reactions
    return out


async def create_user_event(payload: dict) -> dict:
    ts = payload.get("release_ts") or payload.get("ts")
    dt = datetime.fromisoformat(str(ts)) if ts else datetime.now(UTC)
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    end = payload.get("end_ts")
    end_dt = datetime.fromisoformat(str(end)) if end else None
    if end_dt is not None and end_dt.tzinfo is not None:
        end_dt = end_dt.astimezone(UTC).replace(tzinfo=None)
    title = str(payload.get("title") or "User event").strip()
    dic = load_dictionary()
    key = payload.get("event_key") or f"user.{dic.fallback_key(title, payload.get('country', 'GLOBAL'))}"
    async with session_scope() as s:
        row = Event(
            event_key=key,
            kind=payload.get("kind", "risk_window" if end_dt else "geo"),
            country=str(payload.get("country") or "GLOBAL").upper(),
            currency=payload.get("currency"),
            title=title,
            category=payload.get("category", "other"),
            importance=int(payload.get("importance", 2)),
            release_ts=dt,
            release_date=dt.date(),
            end_ts=end_dt,
            reference_period=str(payload.get("reference_period") or dt.date().isoformat()),
            status="scheduled",
            source_provider="user",
            source_url=payload.get("source_url"),
            linked_series=list(payload.get("linked_series") or []),
            affected_tickers=[t.upper() for t in payload.get("affected_tickers") or []],
            notes=payload.get("notes"),
        )
        s.add(row)
        await s.flush()
        if any(payload.get(k) is not None for k in ("consensus", "actual", "previous")):
            a, c = payload.get("consensus"), payload.get("actual")
            s.add(
                EventValue(
                    event_id=row.id,
                    consensus=a,
                    actual=c,
                    previous=payload.get("previous"),
                    unit=payload.get("unit"),
                    consensus_source="user" if a is not None else None,
                    actual_source="user" if c is not None else None,
                    surprise=(c - a) if a is not None and c is not None else None,
                )
            )
            if c is not None:
                row.status = "released"
        rid = row.id
    return (await get_event(rid)) or {}


async def delete_event(event_id: int) -> bool:
    async with session_scope() as s:
        row = await s.get(Event, event_id)
        if row is None:
            return False
        await s.execute(delete(EventValue).where(EventValue.event_id == event_id))
        await s.delete(row)
        return True


async def risk_windows(asof: date | None = None) -> list[dict]:
    """Active + upcoming user risk windows (also consumed by regime analysis)."""
    asof = asof or date.today()
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(Event)
                    .where(Event.kind == "risk_window")
                    .options(selectinload(Event.values))
                    .order_by(Event.release_ts)
                )
            )
            .scalars()
            .all()
        )
    return [dump_event(r) for r in rows if (r.end_ts or r.release_ts).date() >= asof - timedelta(days=30)]


async def week(
    start: date | None = None, countries: list[str] | None = None, min_importance: int = 1
) -> dict:
    start = start or (date.today() - timedelta(days=date.today().weekday()))
    end = start + timedelta(days=6)
    evs = await list_events(start, end, countries=countries, min_importance=min_importance)
    days = [(start + timedelta(days=i)).isoformat() for i in range(7)]
    grid: dict[str, dict[str, list[dict]]] = {}
    for e in evs:
        d = (
            e["release_ts"].date().isoformat()
            if isinstance(e["release_ts"], datetime)
            else str(e["release_ts"])[:10]
        )
        cell = grid.setdefault(e["country"], {dd: [] for dd in days}).setdefault(d, [])
        cell.append(
            {
                "id": e["id"],
                "title": e["title"],
                "kind": e["kind"],
                "importance": e["importance"],
                "status": e["status"],
                "release_ts": e["release_ts"],
                "release_ts_il": e["release_ts_il"],
                "consensus": e["values"]["consensus"],
                "actual": e["values"]["actual"],
                "previous": e["values"]["previous"],
                "surprise": e["values"]["surprise"],
                "surprise_z": e["values"]["surprise_z"],
                "unit": e["values"]["unit"],
            }
        )
    order = ["US", "IL", "EA", "DE", "GB", "JP", "CN", "CA", "CH", "AU", "GLOBAL"]
    rows = [{"country": cc, "flag": FLAGS.get(cc, ""), "days": grid[cc]} for cc in order if cc in grid]
    rows += [
        {"country": cc, "flag": FLAGS.get(cc, ""), "days": g} for cc, g in grid.items() if cc not in order
    ]
    return {"start": start.isoformat(), "end": end.isoformat(), "days": days, "rows": rows, "count": len(evs)}


async def central_banks() -> list[dict]:
    from app.data.series_service import read_series

    out = []
    for cb in next_decisions():
        rate = rate_ts = None
        if cb.get("rate_series"):
            df = read_series(cb["rate_series"])
            if df.height:
                rate, rate_ts = float(df["value"][-1]), df["ts"][-1]
        async with session_scope() as s:
            last = (
                (
                    await s.execute(
                        select(Event)
                        .where(Event.event_key == cb["event_key"], Event.status.in_(("released", "revised")))
                        .options(selectinload(Event.values))
                        .order_by(Event.release_ts.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
        out.append(
            {
                **cb,
                "flag": FLAGS.get(cb["country"], ""),
                "current_rate": rate,
                "rate_ts": rate_ts,
                "last_decision": dump_event(last) if last else None,
            }
        )
    return out


async def prune_central_bank_rows(start: date, end: date) -> int:
    """Delete scheduled central-bank rows in [start, end] that the seed no longer produces.

    Decision rows are keyed by (event_key, date), so correcting a date in seeds/central_banks.yaml would otherwise
    leave the old, wrong row behind next to the new one. Only rows created by the central-bank source, still
    scheduled and without an actual value are removed; user rows and vendor-only rows are never touched.
    """
    from app.calendar.sources.central_banks import CentralBankSource, load_central_banks

    src = CentralBankSource()
    expected = {(e.event_key, e.ts.date()) for e in await src.fetch(start, end)}
    keys = {b["event_key"] for b in load_central_banks(src.path)} | {"us.fomc_minutes", "us.beige_book"}
    removed = 0
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(Event)
                    .where(
                        Event.event_key.in_(sorted(keys)),
                        Event.release_date >= start,
                        Event.release_date <= end,
                        Event.status == "scheduled",
                    )
                    .options(selectinload(Event.values))
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            if src.id not in (r.source_provider or "").split(","):
                continue
            if (r.event_key, r.release_date) in expected or any(v.actual is not None for v in r.values):
                continue
            await s.execute(delete(EventValue).where(EventValue.event_id == r.id))
            await s.delete(r)
            removed += 1
    if removed:
        log.info("calendar: pruned %d stale central-bank rows", removed)
    return removed


def to_ics(events: list[dict]) -> bytes:
    cal = ICal()
    cal.add("prodid", "-//Personal Terminal//calendar//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Terminal economic calendar")
    for e in events:
        ev = IEvent()
        ev.add("uid", f"terminal-event-{e['id']}@local")
        ev.add("summary", f"{e.get('flag', '')} {e['title']}".strip())
        ts = (
            _utc(e["release_ts"])
            if isinstance(e["release_ts"], datetime)
            else datetime.fromisoformat(str(e["release_ts"])).replace(tzinfo=UTC)
        )
        ev.add("dtstart", ts)
        end = e.get("end_ts")
        if end:
            ev.add(
                "dtend",
                _utc(end)
                if isinstance(end, datetime)
                else datetime.fromisoformat(str(end)).replace(tzinfo=UTC),
            )
        else:
            ev.add("dtend", ts + timedelta(minutes=30))
        v = e.get("values") or {}
        desc = [
            f"kind: {e['kind']}",
            f"importance: {e['importance']}",
            f"period: {e.get('reference_period') or '-'}",
        ]
        for k in ("consensus", "actual", "previous"):
            if v.get(k) is not None:
                desc.append(f"{k}: {v[k]} {v.get('unit') or ''}".strip())
        if e.get("source_url"):
            desc.append(e["source_url"])
        ev.add("description", "\n".join(desc))
        cal.add_component(ev)
    return cal.to_ical()


async def refresh(scope: str = "all", start: date | None = None, end: date | None = None) -> dict:
    from app.calendar.bus import get_bus
    from app.calendar.sources import ACTUALS_SOURCES, CORPORATE_SOURCES, OFFICIAL_SOURCES, VENDOR_SOURCES

    today = date.today()
    only = {
        "official": OFFICIAL_SOURCES,
        "vendor": VENDOR_SOURCES,
        "corporate": CORPORATE_SOURCES,
        "actuals": ACTUALS_SOURCES,
    }.get(scope)
    start = start or (today - timedelta(days=7 if scope != "vendor" else 3))
    end = end or (
        today + timedelta(days={"official": 120, "vendor": 14, "corporate": 60, "actuals": 1}.get(scope, 90))
    )
    res = await get_bus().run(start, end, only=only)
    if only is None or "central_banks" in only:
        res["pruned_cb_rows"] = await prune_central_bank_rows(start, end)
    if res.get("keys"):
        from app.calendar.surprise import build_esi

        for cc in sorted({k.split(".")[0].upper() for k in res["keys"]}):
            try:
                await build_esi(cc)
            except Exception as e:
                log.warning("ESI %s failed: %s", cc, e)
    return res
