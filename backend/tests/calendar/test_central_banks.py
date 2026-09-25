"""Central-bank calendar: seed dates, next-decision lookup, live rate from the linked series, stale-row pruning."""

from datetime import date, datetime

import polars as pl

from app.calendar import service
from app.calendar.bus import CalendarBus, set_bus
from app.calendar.models import Event
from app.calendar.sources.central_banks import CentralBankSource, load_central_banks, next_decisions
from app.data.series_service import get_store
from app.data.store.sqlite import session_scope


def _by_bank(today: date) -> dict[str, dict]:
    return {b["bank"]: b for b in next_decisions(today)}


def test_next_boi_decision_after_2026_09_24_is_oct_21():
    nxt = _by_bank(date(2026, 9, 24))
    boi = nxt["boi"]
    assert boi["next_date"] == "2026-10-21"
    assert boi["verified"] is True
    assert boi["days_to_go"] == 27
    assert boi["last_date"] == "2026-09-01"
    # 16:00 Israel (IDT, UTC+3) -> 13:00 UTC
    assert boi["next_ts"] == datetime(2026, 10, 21, 13, 0)


def test_every_bank_has_a_next_decision_and_known_dates():
    nxt = _by_bank(date(2026, 9, 24))
    assert set(nxt) == {"fed", "ecb", "boi", "boe", "boj", "boc", "rba", "snb"}
    expected = {
        "fed": "2026-10-28",
        "ecb": "2026-10-29",
        "boi": "2026-10-21",
        "boe": "2026-11-05",
        "boj": "2026-10-30",
        "boc": "2026-10-28",
        "rba": "2026-09-29",
        "snb": "2026-09-24",
    }
    assert {k: v["next_date"] for k, v in nxt.items()} == expected
    assert nxt["snb"]["days_to_go"] == 0
    assert all(v["verified"] for v in nxt.values())
    # a year later the 2027 rows take over
    later = _by_bank(date(2027, 9, 1))
    assert later["boi"]["next_date"] == "2027-09-27" and later["fed"]["next_date"] == "2027-09-15"


def test_seed_is_sorted_unique_and_verified_from_sep_2026():
    for bank in load_central_banks():
        ds = [d["date"] for d in bank["decisions"]]
        assert ds == sorted(ds) and len(ds) == len(set(ds)), bank["bank"]
        assert all(d.get("verified", True) for d in bank["decisions"] if d["date"] >= date(2026, 9, 1)), bank[
            "bank"
        ]
    boi = next(b for b in load_central_banks() if b["bank"] == "boi")
    boi_dates = {d["date"] for d in boi["decisions"]}
    assert date(2026, 10, 5) not in boi_dates and date(2026, 8, 24) not in boi_dates
    assert {date(2026, 9, 1), date(2026, 10, 21), date(2027, 8, 18)} <= boi_dates


async def test_central_banks_service_reads_rate_from_linked_series(db, monkeypatch):
    import app.calendar.service as svc

    monkeypatch.setattr(svc, "next_decisions", lambda: next_decisions(date(2026, 9, 24)))
    df = pl.DataFrame(
        {"ts": [datetime(2026, 9, 21), datetime(2026, 9, 22)], "value": [3.88, 3.87]}
    ).with_columns(pl.col("ts").cast(pl.Datetime("us")))
    get_store().write("fred:DFF", df)
    rows = {b["bank"]: b for b in await service.central_banks()}
    assert rows["fed"]["current_rate"] == 3.87 and rows["fed"]["rate_ts"] == datetime(2026, 9, 22)
    assert rows["boi"]["next_date"] == "2026-10-21"
    assert rows["ecb"]["current_rate"] is None  # no data stored for fred:ECBDFR in this test


async def test_refresh_prunes_stale_central_bank_rows(db):
    # a row from an older seed (wrong date) and a user row on the same key/date must be treated differently
    async with session_scope() as s:
        for src, title in (("central_banks", "Bank of Israel rate decision"), ("user", "My BoI note")):
            s.add(
                Event(
                    event_key="il.boi",
                    kind="cb_decision",
                    country="IL",
                    title=title,
                    category="rates",
                    importance=3,
                    release_ts=datetime(2026, 10, 5, 13, 0),
                    release_date=date(2026, 10, 5),
                    reference_period="2026-10-05" if src != "user" else "user-note",
                    status="scheduled",
                    source_provider=src,
                )
            )
    set_bus(CalendarBus([CentralBankSource()]))
    try:
        res = await service.refresh("official", start=date(2026, 9, 17), end=date(2026, 12, 31))
    finally:
        set_bus(None)
    assert res["pruned_cb_rows"] == 1
    evs = await service.list_events(
        date(2026, 9, 17), date(2026, 12, 31), countries=["IL"], kinds=["cb_decision"]
    )
    got = sorted((e["release_ts"].date().isoformat(), e["source_provider"]) for e in evs)
    assert got == [("2026-10-05", "user"), ("2026-10-21", "central_banks"), ("2026-11-23", "central_banks")]
    # idempotent
    assert await service.prune_central_bank_rows(date(2026, 9, 17), date(2026, 12, 31)) == 0
