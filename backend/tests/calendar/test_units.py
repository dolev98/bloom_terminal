"""ForexFactory unit handling: units come from the dictionary, the value suffix, or a "m/m"-style title."""

from datetime import date, datetime

from app.calendar.bus import upsert_events
from app.calendar.service import list_events
from app.calendar.sources.forexfactory import ForexFactorySource, ff_unit, parse_unit
from app.calendar.types import NormalizedEvent


def test_parse_unit_and_ff_unit():
    assert parse_unit("0.3%") == "%" and parse_unit("201K") == "k" and parse_unit("-0.7M") == "M"
    assert parse_unit("15.2B") == "bn" and parse_unit("", None) is None and parse_unit("", "-2.0%") == "%"
    assert ff_unit("Core Retail Sales m/m", "-0.5", "0.6") == "%"
    assert ff_unit("BOJ Core CPI y/y", "", "") == "%" and ff_unit("GDP q/q", None, None) == "%"
    assert ff_unit("New Home Sales", "615K", "607K") == "k"
    assert ff_unit("Richmond Manufacturing Index", "2", "4") is None
    assert ff_unit("Unemployment Rate", "4.3", "4.3", dictionary_unit="%") == "%"


def test_ff_parse_sets_units_for_unmapped_titles():
    rows = [
        {
            "title": "Core Retail Sales m/m",
            "country": "CAD",
            "date": "2026-09-24T08:30:00-04:00",
            "impact": "Medium",
            "forecast": "-0.5",
            "previous": "0.5",
        },
        {
            "title": "Crude Oil Inventories",
            "country": "USD",
            "date": "2026-09-23T10:30:00-04:00",
            "impact": "Low",
            "forecast": "-0.7M",
            "previous": "-0.6M",
        },
        {
            "title": "Richmond Manufacturing Index",
            "country": "USD",
            "date": "2026-09-22T10:00:00-04:00",
            "impact": "Low",
            "forecast": "2",
            "previous": "4",
        },
    ]
    evs = {e.title: e for e in ForexFactorySource().parse(rows, date(2026, 9, 21), date(2026, 9, 27))}
    assert evs["Core Retail Sales m/m"].unit == "%" and evs["Core Retail Sales m/m"].consensus == -0.5
    assert evs["Crude Oil Inventories"].unit == "M" and evs["Crude Oil Inventories"].previous == -0.6
    assert evs["Richmond Manufacturing Index"].unit is None


async def test_upsert_fills_missing_unit_without_new_vintage(db):
    def ev(unit):
        return NormalizedEvent(
            event_key="ca.core_retail_sales_mom",
            kind="macro",
            country="CA",
            title="Core Retail Sales m/m",
            ts=datetime(2026, 9, 24, 12, 30),
            importance=2,
            unit=unit,
            consensus=-0.5,
            previous=0.5,
            source="forexfactory",
            tier="forexfactory",
        )

    await upsert_events([ev(None)])
    res = await upsert_events([ev("%")])
    assert res["vintages"] == 0
    (row,) = await list_events(date(2026, 9, 24), date(2026, 9, 24))
    assert row["values"]["unit"] == "%" and row["values"]["consensus"] == -0.5


async def test_upsert_merges_source_from_a_second_provider(db):
    # regression: merging a second provider's copy of an event crashed (list - set) and aborted the refresh
    def ev(source):
        return NormalizedEvent(
            event_key="us.initial_jobless_claims",
            kind="macro",
            country="US",
            title="Initial jobless claims",
            ts=datetime(2026, 9, 24, 12, 30),
            importance=2,
            unit="k",
            consensus=201.0,
            source=source,
            tier=source,
        )

    await upsert_events([ev("dol")])
    await upsert_events([ev("forexfactory")])
    (row,) = await list_events(date(2026, 9, 24), date(2026, 9, 24))
    assert row["source_provider"] == "dol,forexfactory"
