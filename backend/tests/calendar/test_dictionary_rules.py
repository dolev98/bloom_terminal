from datetime import date, datetime, timedelta
from pathlib import Path

from app.calendar.dictionary import (
    fix_period_year,
    load_dictionary,
    normalize,
    period_from_rule,
    period_from_text,
)
from app.calendar.rules import (
    IsraelCalendar,
    beige_book_date,
    cbs_release_ts,
    fomc_minutes_date,
    opex_dates,
    tase_reporting_deadlines,
    third_friday,
)
from app.calendar.sources.central_banks import load_central_banks, next_decisions
from app.calendar.sources.opex import nyse_holidays


def test_dictionary_mapping():
    d = load_dictionary()
    assert d.lookup("Non-Farm Employment Change", "US").event_key == "us.nfp"
    assert d.lookup("Nonfarm Payrolls", "US").event_key == "us.nfp"
    assert d.lookup("NFP", "US").event_key == "us.nfp"
    assert d.lookup("CPI m/m", "US").event_key == "us.cpi"
    assert d.lookup("CPI MoM (Aug)", "US").event_key == "us.cpi"
    assert d.lookup("Core CPI y/y", "US").event_key == "us.core_cpi_yoy"
    assert d.lookup("Inflation Rate MoM", "IL").event_key == "il.cpi"
    assert d.lookup("Federal Funds Rate", "US").kind == "cb_decision"
    assert d.lookup("Something Unknown", "US") is None
    assert d.by_release("Consumer Price Index", "US").event_key == "us.cpi"
    assert d.by_release("Employment Situation", "US").event_key == "us.nfp"
    assert d.by_release("Gross Domestic Product", "US").event_key == "us.gdp"
    assert d.country_from_currency("USD") == "US" and d.country_from_currency("ILS") == "IL"
    assert d.country_from_name("United States") == "US" and d.country_from_name("EA") == "EA"
    assert d.fallback_key("Factory Orders MoM", "DE") == "de.factory_orders_mom"
    assert normalize("CPI m/m (Aug)") == "cpi mom"
    keys = [i.event_key for i in d.indicators]
    assert len(keys) == len(set(keys))


def test_period_helpers():
    assert period_from_text("August 2026") == "2026-08"
    assert period_from_text("2nd Quarter 2026") == "2026Q2"
    assert period_from_text("Q3 2026") == "2026Q3"
    assert fix_period_year(period_from_text("Sep"), date(2026, 10, 2)) == "2026-09"
    assert fix_period_year(period_from_text("Dec"), date(2027, 1, 12)) == "2026-12"
    assert fix_period_year(period_from_text("Q4"), date(2027, 1, 28)) == "2026Q4"
    assert period_from_rule("prev_month", date(2026, 10, 13)) == "2026-09"
    assert period_from_rule("prev_quarter", date(2026, 10, 29)) == "2026Q3"
    assert period_from_rule("prev_quarter", date(2027, 1, 28)) == "2026Q4"
    assert period_from_rule("prev_week", date(2026, 10, 1)) == "2026-09-26"  # Thursday -> prior Saturday


def test_cbs_rule_15th_friday_and_holiday_eve():
    cal = IsraelCalendar(holidays=frozenset(), eves=frozenset())
    # May 15 2026 is a Friday -> Thursday May 14, 14:00 IL (11:00 UTC in IDT)
    assert date(2026, 5, 15).weekday() == 4
    ts = cbs_release_ts(date(2026, 5, 15), cal)
    assert ts == datetime(2026, 5, 14, 11, 0)
    # normal weekday: 18:30 IL = 15:30 UTC (IDT)
    assert date(2026, 7, 15).weekday() == 2
    assert cbs_release_ts(date(2026, 7, 15), cal) == datetime(2026, 7, 15, 15, 30)
    # holiday eve on the 15th (Wednesday) -> Tuesday 14th 14:00
    cal2 = IsraelCalendar(holidays=frozenset({date(2026, 7, 16)}), eves=frozenset({date(2026, 7, 15)}))
    assert cbs_release_ts(date(2026, 7, 15), cal2) == datetime(2026, 7, 14, 11, 0)
    # non-price release: 13:00 IL
    assert cbs_release_ts(date(2026, 7, 15), cal, price_index=False) == datetime(2026, 7, 15, 10, 0)
    # Sunday is eligible in Israel
    assert date(2026, 11, 15).weekday() == 6
    assert cbs_release_ts(date(2026, 11, 15), cal) == datetime(2026, 11, 15, 16, 30)  # IST


def test_opex_juneteenth_2026_moves_to_thursday():
    hol = set(nyse_holidays([2026]))
    assert date(2026, 6, 19) in hol and third_friday(2026, 6) == date(2026, 6, 19)
    by_month = {d.month: (d, quad) for d, quad in opex_dates(2026, hol)}
    assert by_month[6] == (date(2026, 6, 18), True)
    assert by_month[3] == (date(2026, 3, 20), True)
    assert by_month[7] == (date(2026, 7, 17), False)


def test_tase_deadlines():
    dl = tase_reporting_deadlines(2026)
    assert [d for d, _, _ in dl] == [
        date(2026, 3, 31),
        date(2026, 5, 31),
        date(2026, 8, 31),
        date(2026, 11, 30),
    ]
    assert dl[0][1] == "FY" and dl[3][1] == "Q3"


def test_central_bank_yaml_and_fomc_minutes_rule():
    banks = {b["bank"]: b for b in load_central_banks()}
    fed = banks["fed"]
    dates = [d["date"] for d in fed["decisions"]]
    assert date(2026, 9, 16) in dates and date(2027, 12, 8) in dates
    assert all(d.get("verified") for d in fed["decisions"])
    assert fomc_minutes_date(date(2026, 9, 16)) == date(2026, 10, 7)
    assert beige_book_date(date(2026, 10, 28)) == date(2026, 10, 14)
    ecb = banks["ecb"]
    assert date(2026, 10, 29) in [d["date"] for d in ecb["decisions"]] and ecb["decision_time"] == "14:15"
    for b in banks.values():
        assert b["source_url"] and b["verified_on"]
        for d in b["decisions"]:
            assert "verified" in d
    nd = {x["bank"]: x for x in next_decisions(date(2026, 9, 24))}
    assert nd["fed"]["next_date"] == "2026-10-28" and nd["fed"]["days_to_go"] == 34
    assert nd["ecb"]["next_date"] == "2026-10-29"


async def test_central_bank_source_emits_minutes_and_beige_book():
    from app.calendar.sources.central_banks import CentralBankSource

    evs = await CentralBankSource().fetch(date(2026, 10, 1), date(2026, 11, 15))
    keys = {(e.event_key, e.ts.date()) for e in evs}
    assert ("us.fomc", date(2026, 10, 28)) in keys
    assert ("us.fomc_minutes", date(2026, 11, 18)) not in keys  # outside window
    assert ("us.beige_book", date(2026, 10, 14)) in keys
    assert ("us.fomc_minutes", date(2026, 10, 7)) in keys  # Sep 16 meeting + 3 weeks
    fomc = next(e for e in evs if e.event_key == "us.fomc")
    assert fomc.ts == datetime(2026, 10, 28, 18, 0) and fomc.kind == "cb_decision" and fomc.tier == "official"


async def test_cbs_and_tase_sources_offline():
    from app.calendar.sources.cbs_rules import CbsRulesSource, TaseSource
    from app.data.providers.base import Holiday

    hol = [
        Holiday(
            date=date(2026, 9, 21), name="Yom Kippur", country="IL", exchange_closed=True, source="hebcal"
        ),
        Holiday(
            date=date(2026, 9, 20),
            name="Erev Yom Kippur",
            country="IL",
            exchange_closed=True,
            source="hebcal",
        ),
    ]
    evs = await CbsRulesSource(holidays=hol).fetch(date(2026, 9, 1), date(2026, 11, 30))
    cpi = [e for e in evs if e.event_key == "il.cpi"]
    assert [e.reference_period for e in cpi] == ["2026-08", "2026-09", "2026-10"]
    assert cpi[0].ts == datetime(2026, 9, 15, 15, 30)
    tase = await TaseSource(holidays=hol, tickers=["TEVA.TA"]).fetch(date(2026, 9, 1), date(2026, 11, 30))
    kinds = {e.kind for e in tase}
    assert kinds == {"reporting_deadline", "holiday"}
    dl = next(e for e in tase if e.kind == "reporting_deadline")
    assert dl.ts.date() == date(2026, 11, 30) and dl.affected_tickers == ["TEVA.TA"]


def test_seed_files_have_provenance():
    from app.calendar.sources.geo import load_geo_events

    for r in load_geo_events():
        assert r["source_url"] and r["verified_on"] and "verified" in r
    assert Path(__file__).parents[2].joinpath("app/calendar/seeds/exchange_holidays.yaml").exists()
    assert timedelta(days=1)  # keep import used
