import json
from datetime import date, datetime
from pathlib import Path

import respx
from httpx import Response

from app.calendar.sources.bls_bea import BeaSource, BlsIcsSource, parse_bea_json, parse_bls_ics
from app.calendar.sources.fmp import FmpEconomicSource, parse_corporate, parse_economic
from app.calendar.sources.forexfactory import ForexFactoryDenied, ForexFactorySource, parse_ff, parse_number
from app.calendar.sources.geo import MsciSource, parse_msci_csv
from app.calendar.sources.opex import NagerHolidaysSource
from app.calendar.sources.treasury import TreasuryAuctionSource, auction_event

FX = Path(__file__).with_name("fixtures")


def test_bls_ics_parse_and_map():
    text = (FX / "bls.ics").read_text()
    rows = parse_bls_ics(text)
    assert ("Consumer Price Index", datetime(2026, 10, 13, 12, 30)) in rows  # 08:30 EDT -> 12:30 UTC
    evs = BlsIcsSource().parse(text, date(2026, 10, 1), date(2026, 10, 31))
    by = {e.event_key: e for e in evs}
    assert by["us.cpi"].reference_period == "2026-09" and by["us.cpi"].tier == "official"
    assert by["us.nfp"].ts == datetime(2026, 10, 2, 12, 30) and "fred:PAYEMS" in by["us.nfp"].linked_series
    assert "us.jolts" not in by  # not in the dictionary -> skipped


@respx.mock
async def test_bls_ics_fetch_uses_mailto_ua():
    route = respx.get("https://www.bls.gov/schedule/news_release/bls.ics").mock(
        return_value=Response(200, text=(FX / "bls.ics").read_text())
    )
    evs = await BlsIcsSource().fetch(date(2026, 10, 1), date(2026, 10, 31))
    assert len(evs) == 2 and "+mailto:" in route.calls[0].request.headers["User-Agent"]


@respx.mock
async def test_bea_json():
    data = json.loads((FX / "bea.json").read_text())
    rows = parse_bea_json(data)
    assert len([r for r in rows if r[0] == "Gross Domestic Product"]) == 2  # duplicate collapsed
    respx.get("https://apps.bea.gov/API/signup/release_dates.json").mock(
        return_value=Response(200, json=data)
    )
    evs = await BeaSource().fetch(date(2026, 10, 1), date(2026, 11, 30))
    gdp = [e for e in evs if e.event_key == "us.gdp"]
    assert (
        len(gdp) == 2 and gdp[0].ts == datetime(2026, 10, 29, 12, 30) and gdp[0].reference_period == "2026Q3"
    )
    assert any(e.event_key == "us.pce" for e in evs)


@respx.mock
async def test_treasury_upcoming_and_results():
    respx.get("https://www.treasurydirect.gov/TA_WS/securities/upcoming").mock(
        return_value=Response(200, json=json.loads((FX / "treasury_upcoming.json").read_text()))
    )
    respx.get("https://www.treasurydirect.gov/TA_WS/securities/auctioned").mock(
        return_value=Response(200, json=json.loads((FX / "treasury_auctioned.json").read_text()))
    )
    evs = await TreasuryAuctionSource().fetch(date(2026, 10, 1), date(2026, 10, 31))
    assert len(evs) == 3
    note5 = next(e for e in evs if e.raw["cusip"] == "91282CRN3")
    assert (
        note5.ts == datetime(2026, 10, 6, 17, 0)
        and note5.status == "scheduled"
        and "$70bn" in note5.title
        and note5.reference_period == "91282CRN3"
    )
    bill = next(e for e in evs if e.raw["cusip"] == "912797QX1")
    assert bill.ts == datetime(2026, 10, 5, 15, 30)
    done = next(e for e in evs if e.raw["cusip"] == "91282CRQ6")
    assert (
        done.actual == 4.787
        and done.status == "released"
        and done.forecast_alt == 2.63
        and "bid-to-cover 2.63" in done.notes
        and "indirect 49%" in done.notes
    )
    assert auction_event({"auctionDate": "bad"}, "t", "official") is None


def test_forexfactory_parse():
    rows = parse_ff((FX / "ff_week.json").read_text())
    evs = ForexFactorySource().parse(rows, date(2026, 10, 1), date(2026, 10, 7))
    by = {e.event_key: e for e in evs}
    assert (
        by["us.nfp"].consensus == 120.0
        and by["us.nfp"].previous == 73.0
        and by["us.nfp"].consensus_source == "forexfactory"
    )
    assert by["us.nfp"].ts == datetime(2026, 10, 2, 12, 30) and by["us.nfp"].reference_period == ""
    assert by["us.unemployment"].consensus == 4.3
    assert by["gb.rightmove_hpi_mom"].kind == "macro" and by["gb.rightmove_hpi_mom"].previous == -2.0
    assert by["jp.holiday.2026-10-05"].kind == "holiday"
    assert parse_number("-0.3%") == -0.3 and parse_number("") is None
    try:
        parse_ff("<html>Request Denied</html>")
        raise AssertionError("expected ForexFactoryDenied")
    except ForexFactoryDenied:
        pass


@respx.mock
async def test_forexfactory_fetch_caches_and_handles_denial(db):
    route = respx.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json").mock(
        return_value=Response(200, text=(FX / "ff_week.json").read_text())
    )
    src = ForexFactorySource()
    a = await src.fetch(date(2026, 10, 1), date(2026, 10, 7))
    b = await src.fetch(date(2026, 10, 1), date(2026, 10, 7))
    assert len(a) == len(b) == 4 and route.call_count == 1  # cached for an hour
    from app.state import get_cache

    get_cache()._mem.clear()
    from sqlalchemy import delete

    from app.data.store.models import ResponseCache
    from app.data.store.sqlite import session_scope

    async with session_scope() as s:
        await s.execute(delete(ResponseCache))
    route.mock(return_value=Response(200, text="Request Denied"))
    assert await src.fetch(date(2026, 10, 1), date(2026, 10, 7)) == []


@respx.mock
async def test_fmp_economic_and_corporate(settings, monkeypatch):
    rows = json.loads((FX / "fmp_econ.json").read_text())
    evs = parse_economic(rows, date(2026, 10, 1), date(2026, 10, 31))
    by = {e.event_key: e for e in evs}
    nfp = by["us.nfp"]
    assert (
        nfp.actual == 140
        and nfp.consensus == 118
        and nfp.reference_period == "2026-09"
        and nfp.status == "released"
        and nfp.ts == datetime(2026, 10, 2, 12, 30)
    )
    assert by["il.cpi"].consensus == 0.2 and by["il.cpi"].country == "IL" and by["il.cpi"].actual is None
    assert (
        by["de.factory_orders_mom"].importance == 2
        and by["de.factory_orders_mom"].title == "Factory Orders MoM"
    )
    # no key -> clean skip, no HTTP
    monkeypatch.setattr(settings, "fmp_api_key", "")
    assert await FmpEconomicSource().fetch(date(2026, 10, 1), date(2026, 10, 31)) == []
    monkeypatch.setattr(settings, "fmp_api_key", "k")
    route = respx.get("https://financialmodelingprep.com/stable/economic-calendar").mock(
        return_value=Response(200, json=rows)
    )
    evs = await FmpEconomicSource().fetch(date(2026, 8, 1), date(2026, 12, 31))  # > 90 days -> 2 windows
    assert route.call_count == 2 and len(evs) == 4
    earn = parse_corporate(
        "earnings",
        [
            {
                "symbol": "AAPL",
                "date": "2026-10-29",
                "epsActual": None,
                "epsEstimated": 1.62,
                "revenueEstimated": 1.0e11,
                "time": "amc",
            }
        ],
        date(2026, 10, 1),
        date(2026, 10, 31),
        {"AAPL"},
    )
    assert (
        earn[0].event_key == "earnings.AAPL"
        and earn[0].consensus == 1.62
        and earn[0].ts == datetime(2026, 10, 29, 20, 30)
    )


@respx.mock
async def test_msci_and_nager():
    text = (FX / "msci.csv").read_text()
    recs = parse_msci_csv(text)
    assert recs[0]["announcement"] == date(2026, 11, 11) and recs[0]["effective"] == date(2026, 12, 1)
    respx.get("https://app2.msci.com/eqb/pressreleases/archive/ir_dates.csv").mock(
        return_value=Response(200, text=text)
    )
    evs = await MsciSource().fetch(date(2026, 11, 1), date(2026, 12, 31))
    assert {e.event_key for e in evs} == {"global.msci_announcement", "global.msci_effective"}
    respx.get(url__regex=r"https://date\.nager\.at/api/v3/PublicHolidays/2026/GB").mock(
        return_value=Response(
            200,
            json=[
                {
                    "date": "2026-12-25",
                    "localName": "Christmas Day",
                    "name": "Christmas Day",
                    "countryCode": "GB",
                    "global": True,
                    "types": ["Public"],
                },
                {
                    "date": "2026-11-30",
                    "name": "St Andrew's Day",
                    "countryCode": "GB",
                    "global": False,
                    "counties": ["GB-SCT"],
                    "types": ["Public"],
                },
            ],
        )
    )
    respx.get(url__regex=r"https://date\.nager\.at/api/v3/PublicHolidays/2026/DE").mock(
        return_value=Response(500)
    )
    respx.get(url__regex=r"https://nagerholidays\.com/api/v4/Holidays/DE/2026").mock(
        return_value=Response(
            200,
            json=[
                {
                    "date": "2026-12-25",
                    "name": "Christmas Day",
                    "countryCode": "DE",
                    "nationalHoliday": True,
                    "holidayTypes": ["Public"],
                }
            ],
        )
    )
    evs = await NagerHolidaysSource(countries=("GB", "DE")).fetch(date(2026, 11, 1), date(2026, 12, 31))
    assert {(e.country, e.ts.date()) for e in evs} == {("GB", date(2026, 12, 25)), ("DE", date(2026, 12, 25))}


@respx.mock
async def test_fred_releases_with_alfred_actual(db):
    from app.calendar.sources.fred_releases import FredReleasesSource
    from app.data.catalog.loader import load_seed

    await load_seed()
    respx.get("https://api.stlouisfed.org/fred/releases/dates").mock(
        return_value=Response(
            200,
            json={
                "release_dates": [
                    {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-09-11"},
                    {"release_id": 999, "release_name": "Weird Release", "date": "2026-09-12"},
                ]
            },
        )
    )
    respx.get("https://api.stlouisfed.org/fred/release/series").mock(
        return_value=Response(
            200, json={"seriess": [{"id": "CPIAUCSL"}, {"id": "CPILFESL"}, {"id": "CUUR0000SA0"}]}
        )
    )
    respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=Response(
            200,
            json={
                "observations": [
                    {"date": "2026-06-01", "value": "100.0"},
                    {"date": "2026-07-01", "value": "101.0"},
                    {"date": "2026-08-01", "value": "101.505"},
                ]
            },
        )
    )
    evs = await FredReleasesSource().fetch(date(2026, 9, 1), date(2026, 9, 30))
    assert len(evs) == 1
    e = evs[0]
    assert e.event_key == "us.cpi" and e.reference_period == "2026-08" and e.actual_source == "alfred"
    assert abs(e.actual - 0.5) < 1e-6 and abs(e.previous - 1.0) < 1e-6
    assert set(e.linked_series) == {"fred:CPIAUCSL", "fred:CPILFESL"}  # CUUR0000SA0 not in catalog


@respx.mock
async def test_alfred_actual_is_scaled_to_the_event_unit(db):
    # ICSA is stored as a count; the calendar (and ForexFactory's consensus) use thousands
    from app.calendar.sources.fred_releases import FredReleasesSource
    from app.data.catalog.loader import load_seed

    await load_seed()
    respx.get("https://api.stlouisfed.org/fred/releases/dates").mock(
        return_value=Response(
            200,
            json={
                "release_dates": [
                    {
                        "release_id": 180,
                        "release_name": "Unemployment Insurance Weekly Claims Report",
                        "date": "2026-09-24",
                    }
                ]
            },
        )
    )
    respx.get("https://api.stlouisfed.org/fred/release/series").mock(
        return_value=Response(200, json={"seriess": [{"id": "ICSA"}]})
    )
    respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=Response(
            200,
            json={
                "observations": [
                    {"date": "2026-09-12", "value": "198000"},
                    {"date": "2026-09-19", "value": "197000"},
                ]
            },
        )
    )
    (e,) = await FredReleasesSource().fetch(date(2026, 9, 24), date(2026, 9, 24))
    assert e.event_key == "us.claims" and e.unit == "k"
    assert e.actual == 197.0 and e.previous == 198.0


def test_auction_titles_name_tips_and_frns():
    base = {"auctionDate": "2026-09-17T00:00:00", "securityType": "Note", "reopening": "Yes", "cusip": "X"}
    tips = auction_event(
        {**base, "type": "TIPS", "securityTerm": "9-Year 10-Month", "highYield": "2.6530"}, "t", "t"
    )
    frn = auction_event(
        {**base, "type": "FRN", "securityTerm": "1-Year 10-Month", "highDiscountMargin": "0.04"}, "t", "t"
    )
    note = auction_event({**base, "type": "Note", "securityTerm": "5-Year", "highYield": "5.033"}, "t", "t")
    assert tips.title.startswith("9-Year 10-Month TIPS auction") and tips.actual == 2.653
    assert frn.title.startswith("1-Year 10-Month floating-rate note auction") and frn.actual == 0.04
    assert note.title.startswith("5-Year Note auction")
