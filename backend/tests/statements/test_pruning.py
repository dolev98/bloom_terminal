"""Regression: stray cover-page instants must not create phantom Q/TTM periods; content-less periods are pruned."""

from datetime import date

import respx
from httpx import Response

from app.statements import service
from app.statements.canonical import prune_empty_periods, resolve
from app.statements.concept_map import ConceptMapper
from app.statements.edgar_xbrl import companyfacts_to_facts
from app.statements.fields import FactRow
from app.statements.models import Entity
from tests.statements.conftest import _f


def test_cover_page_instant_snaps_to_real_period(companyfacts):
    dei = companyfacts["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"]
    dei.append(_f(None, "2025-02-10", 990, "2025-02-15", "k-24"))  # 10-K cover date, after FYE
    dei.append(_f(None, "2024-07-17", 995, "2024-08-01", "q2-24", "10-Q"))  # 10-Q cover date
    companyfacts["facts"]["us-gaap"]["Goodwill"] = {
        "units": {"USD": [_f(None, "2019-05-05", 7, "2019-06-01", "old")]}
    }
    rows = companyfacts_to_facts(
        companyfacts, ConceptMapper.from_seed(), entity_id="cik:1", ticker="T", fye_month=12
    )
    ends = {(r.period_type, r.period_end) for r in rows}
    assert ("Q", date(2025, 2, 10)) not in ends and ("TTM", date(2025, 2, 10)) not in ends
    assert ("Q", date(2024, 7, 17)) not in ends and not any(r.period_end == date(2019, 5, 5) for r in rows)
    shares = {
        r.period_end: r.value
        for r in rows
        if r.canonical_field == "shares_outstanding" and r.period_type == "Q"
    }
    assert (
        shares[date(2024, 12, 31)] == 1000 and shares[date(2024, 6, 30)] == 995
    )  # attached to the real quarter
    q_ends = sorted(r.period_end for r in rows if r.period_type == "Q" and r.canonical_field == "revenue")
    assert q_ends[-1] == date(2024, 12, 31)
    resolved = prune_empty_periods(resolve(rows, period_type="Q"))
    assert {pend for _, pend in resolved} == {
        r.period_end for r in rows if r.period_type == "Q" and r.canonical_field == "revenue"
    }


async def test_service_drops_periods_without_core_fields(db, companyfacts):
    from app.data.store.sqlite import session_scope

    async with session_scope() as s:
        s.add(
            Entity(
                entity_id="cik:1",
                ticker="TEST",
                name="Test",
                filer_type="us_10k",
                cik=1,
                fiscal_year_end_month=12,
            )
        )
    rows = companyfacts_to_facts(
        companyfacts, ConceptMapper.from_seed(), entity_id="cik:1", ticker="TEST", fye_month=12
    )
    stray = FactRow(
        "cik:1",
        "TEST",
        "goodwill",
        date(2026, 7, 17),
        "Q",
        5.0,
        "manual",
        fiscal_year=2026,
        fiscal_period="Q3",
    )
    stray_ttm = FactRow(
        "cik:1",
        "TEST",
        "shares_outstanding",
        date(2026, 7, 17),
        "TTM",
        5.0,
        "manual",
        fiscal_year=2026,
        fiscal_period="TTM",
    )
    await service.upsert_facts(rows + [stray, stray_ttm])
    await service.recompute("TEST")
    q = await service.get_statements("TEST", "Q")
    assert q["periods"][-1]["period_end"] == "2024-12-31" and all(
        p["period_end"] != "2026-07-17" for p in q["periods"]
    )
    ttm = await service.ratios("TEST", "TTM")
    assert ttm["periods"][-1]["period_end"] == "2024-12-31"
    cov = await service.coverage("TEST")
    assert all(c["period_end"] != date(2026, 7, 17) for c in cov) and cov[0]["period_end"] == date(
        2024, 12, 31
    )


@respx.mock
def test_refresh_attaches_10q_cover_shares_and_prunes_stale_rows(client, companyfacts):
    """Regression (AAPL): 10-Q cover-page shares (dated weeks after quarter end) must land on that quarter and its
    TTM row, and a plain (non-full) refresh must drop edgar rows an older parse keyed at the cover date."""
    import copy

    from tests.statements.test_api import TICKERS, _submissions

    dei = companyfacts["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"]
    dei.append(_f(None, "2024-07-17", 995, "2024-08-01", "q2-24", "10-Q"))  # 10-Q cover date (Q2)
    dei.append(_f(None, "2024-10-18", 992, "2024-11-01", "q3-24", "10-Q"))  # 10-Q cover date (Q3)
    old = copy.deepcopy(companyfacts)
    # the older payload had an instant the current one no longer carries -> its Q/TTM rows become stale
    old["facts"]["us-gaap"]["Assets"]["units"]["USD"].append(
        _f(None, "2025-03-31", 1, "2025-05-01", "q1-25", "10-Q")
    )
    served = {"cf": old}
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(return_value=Response(200, json=TICKERS))
    respx.get("https://data.sec.gov/submissions/CIK0000320193.json").mock(
        return_value=Response(200, json=_submissions(320193, "Apple Inc.", ["10-K", "10-Q"]))
    )
    respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json").mock(
        side_effect=lambda _req: Response(200, json=served["cf"])
    )
    assert client.post("/api/statements/AAPL/refresh").status_code == 200
    served["cf"] = companyfacts
    body = client.post("/api/statements/AAPL/refresh").json()["edgar"]
    assert body["pruned"] == 2  # total_assets Q + TTM at 2025-03-31

    q = client.get("/api/statements/AAPL", params={"period": "Q"}).json()
    shares = dict(
        zip([p["period_end"] for p in q["periods"]], q["fields"]["shares_outstanding"], strict=True)
    )
    assert shares["2024-06-30"] == 995 and shares["2024-09-30"] == 992 and shares["2024-12-31"] == 1000
    assert all(p["period_end"] not in ("2024-07-17", "2024-10-18", "2025-03-31") for p in q["periods"])
    ttm = client.get("/api/statements/AAPL", params={"period": "TTM"}).json()
    assert (
        ttm["periods"][-1]["period_end"] == "2024-12-31" and ttm["fields"]["shares_outstanding"][-1] == 1000
    )
    cov = client.get("/api/statements/AAPL/coverage").json()
    assert all(c["period_end"] != "2025-03-31" for c in cov)


def test_late_month_cover_dates_snap_instead_of_colliding(companyfacts):
    """Regression (AAPL Q1 FY19): a 10-K cover date on day >= 24 (Oct 26 for a Sep FYE) was kept as a real
    period end; it fell into the next fiscal quarter's bucket and displaced that quarter's shares outstanding."""
    companyfacts["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(
        _f("2025-01-01", "2025-03-31", 330, "2025-05-01", "q1-25", "10-Q")
    )
    dei = companyfacts["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"]
    dei.append(_f(None, "2025-01-27", 985, "2025-02-15", "k-24"))  # 10-K cover date, late in the month
    dei.append(
        _f(None, "2025-04-25", 975, "2025-05-01", "q1-25", "10-Q")
    )  # 10-Q cover date, late in the month
    rows = companyfacts_to_facts(
        companyfacts, ConceptMapper.from_seed(), entity_id="cik:1", ticker="T", fye_month=12
    )
    assert not any(r.period_end in (date(2025, 1, 27), date(2025, 4, 25)) for r in rows)
    q_shares = {
        r.period_end: r.value
        for r in rows
        if r.canonical_field == "shares_outstanding" and r.period_type == "Q"
    }
    ttm_shares = {
        r.period_end: r.value
        for r in rows
        if r.canonical_field == "shares_outstanding" and r.period_type == "TTM"
    }
    assert q_shares[date(2025, 3, 31)] == 975 and ttm_shares[date(2025, 3, 31)] == 975
    assert date(2024, 12, 31) in q_shares
