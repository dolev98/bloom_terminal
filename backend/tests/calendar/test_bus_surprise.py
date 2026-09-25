from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.calendar.bus import CalendarBus, merge_events, upsert_events
from app.calendar.models import Event
from app.calendar.surprise import build_esi, esi_from_points, rolling_z, surprise_history
from app.calendar.types import NormalizedEvent
from app.data.series_service import read_series
from app.data.store.sqlite import session_scope


def ev(**kw) -> NormalizedEvent:
    base = dict(
        event_key="us.cpi",
        kind="macro",
        country="US",
        title="CPI",
        ts=datetime(2026, 10, 13, 12, 30),
        reference_period="2026-09",
        source="x",
        tier="vendor",
    )
    return NormalizedEvent(**{**base, **kw})


def test_merge_priority_and_attach_without_period():
    official = ev(
        source="bls_ics",
        tier="official",
        title="CPI (MoM)",
        ts=datetime(2026, 10, 13, 12, 30),
        actual=0.3,
        previous=0.2,
    )
    vendor = ev(
        source="fmp_econ",
        tier="vendor",
        title="CPI MoM",
        ts=datetime(2026, 10, 13, 12, 31),
        actual=0.4,
        consensus=0.25,
        previous=0.21,
    )
    ff = ev(
        source="forexfactory",
        tier="forexfactory",
        title="CPI m/m",
        ts=datetime(2026, 10, 13, 12, 30),
        reference_period="",
        consensus=0.2,
        previous=0.2,
    )
    other = ev(
        event_key="us.nfp", source="fmp_econ", ts=datetime(2026, 10, 2, 12, 30), reference_period="2026-09"
    )
    merged = merge_events([ff, vendor, official, other])
    assert len(merged) == 2
    cpi = next(m for m in merged if m.event_key == "us.cpi")
    assert cpi.actual == 0.3 and cpi.actual_source == "bls_ics"  # official wins actual
    assert cpi.consensus == 0.25 and cpi.consensus_source == "fmp_econ"  # vendor wins consensus over FF
    assert cpi.ts == datetime(2026, 10, 13, 12, 30) and cpi.title == "CPI (MoM)"
    assert cpi.status == "released" and set(cpi.raw["sources"]) == {"bls_ics", "fmp_econ", "forexfactory"}


async def test_upsert_creates_vintages_and_surprise(db):
    r1 = await upsert_events([ev(consensus=0.2, previous=0.1)])
    assert r1["inserted"] == 1 and r1["vintages"] == 1
    r2 = await upsert_events([ev(consensus=0.2, previous=0.1)])  # unchanged -> no vintage
    assert r2["updated"] == 1 and r2["vintages"] == 0
    r3 = await upsert_events([ev(actual=0.5, actual_source="fmp")])
    assert r3["vintages"] == 1 and r3["keys"] == ["us.cpi"]
    async with session_scope() as s:
        row = (await s.execute(select(Event).options(selectinload(Event.values)))).scalars().one()
        assert row.status == "released" and len(row.values) == 2
        assert (
            row.values[-1].actual == 0.5
            and row.values[-1].consensus == 0.2
            and abs(row.values[-1].surprise - 0.3) < 1e-9
        )
    r4 = await upsert_events([ev(actual=0.6)])
    async with session_scope() as s:
        row = (await s.execute(select(Event))).scalars().one()
        assert row.status == "revised" and r4["vintages"] == 1
    # rescheduled release (same key/period, new date) updates the row instead of duplicating it
    await upsert_events([ev(ts=datetime(2026, 10, 14, 12, 30))])
    async with session_scope() as s:
        rows = (await s.execute(select(Event))).scalars().all()
        assert len(rows) == 1 and rows[0].release_ts == datetime(2026, 10, 14, 12, 30)


def test_rolling_z_window():
    s = [0.1, -0.1, 0.2, -0.2, 0.1, -0.1, 0.5]
    z = rolling_z(s, window=24, min_history=4)
    assert z[:3] == [None, None, None] and z[-1] is not None and z[-1] > 1.5


async def test_surprise_z_and_esi_on_synthetic(db):
    class Src:
        id, tier = "fake", "vendor"

        async def fetch(self, start, end):
            out = []
            for i in range(8):
                ts = datetime(2026, 1, 13, 12, 30) + timedelta(days=30 * i)
                out.append(
                    ev(
                        ts=ts,
                        reference_period=f"2026-{i + 1:02d}",
                        consensus=0.2,
                        actual=0.2 + (0.1 if i % 2 else -0.1) * (1 + i / 10),
                        source="fake",
                    )
                )
                out.append(
                    ev(
                        event_key="us.nfp",
                        importance=3,
                        ts=ts,
                        reference_period=f"2026-{i + 1:02d}",
                        consensus=100,
                        actual=100 + (20 if i % 2 else -20),
                        source="fake",
                    )
                )
            return out

    res = await CalendarBus([Src()]).run(datetime(2026, 1, 1).date(), datetime(2026, 12, 31).date())
    assert res["inserted"] == 16 and set(res["keys"]) == {"us.cpi", "us.nfp"}
    hist = await surprise_history("us.cpi")
    assert len(hist) == 8 and hist[-1]["surprise_z"] is not None and hist[0]["surprise_z"] is None
    esi = await build_esi("US")
    assert esi["series_id"] == "derived:ESI_US" and esi["n"] >= 4
    df = read_series("derived:ESI_US")
    assert df.height == esi["n"] and df.columns == ["ts", "value"]
    from app.data.catalog.loader import get_spec

    spec = await get_spec("derived:ESI_US")
    assert spec is not None and spec.enabled is False and spec.provider == "derived"


def test_esi_from_points_weighting():
    from datetime import date

    pts = [(date(2026, 1, 10), 1.0, 1), (date(2026, 1, 20), -1.0, 3), (date(2026, 6, 1), 2.0, 1)]
    df = esi_from_points(pts, window_days=90)
    vals = dict(zip([t.date() for t in df["ts"].to_list()], df["value"].to_list(), strict=True))
    assert vals[date(2026, 1, 10)] == 1.0
    assert abs(vals[date(2026, 1, 20)] - (-0.5)) < 1e-9  # (1*1 + 3*-1)/4
    assert vals[date(2026, 6, 1)] == 2.0  # January points fell out of the 90-day window
