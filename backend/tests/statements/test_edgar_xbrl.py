from datetime import date

from app.statements.canonical import apply_derived, build_frame, frame_to_payload, resolve
from app.statements.concept_map import ConceptMapper
from app.statements.edgar_xbrl import companyfacts_to_facts, fye_month_from_submissions
from app.statements.periods import approx_start, fiscal_year_of, months_between, quarter_of


def _rows(companyfacts):
    return companyfacts_to_facts(
        companyfacts, ConceptMapper.from_seed(), entity_id="cik:1", ticker="TEST", fye_month=12
    )


def _one(rows, field, ptype, end, restated=False):
    hits = [
        r
        for r in rows
        if r.canonical_field == field
        and r.period_type == ptype
        and r.period_end == end
        and r.restated_flag == restated
    ]
    assert len(hits) <= 1, hits
    return hits[0] if hits else None


def test_fiscal_helpers():
    assert fiscal_year_of(date(2023, 12, 30), 9) == 2024 and quarter_of(date(2023, 12, 30), 9) == 1
    assert fiscal_year_of(date(2024, 9, 28), 9) == 2024 and quarter_of(date(2024, 9, 28), 9) == 4
    assert fiscal_year_of(date(2025, 1, 2), 12) == 2024  # 52/53-week year ending Jan 2 -> FY2024
    assert (
        months_between(date(2024, 1, 1), date(2024, 3, 31)) == 3
        and months_between(date(2024, 1, 1), date(2024, 12, 28)) == 12
    )
    assert months_between(date(2023, 1, 1), date(2024, 12, 31)) is None
    assert approx_start(date(2024, 6, 30), 6) == date(2024, 1, 1)
    assert (
        fye_month_from_submissions({"fiscalYearEnd": "0930"}) == 9 and fye_month_from_submissions({}) is None
    )


def test_mapping_point_in_time_restated_and_dedupe(companyfacts):
    rows = _rows(companyfacts)
    fy23 = _one(rows, "revenue", "FY", date(2023, 12, 31))
    assert fy23.value == 1000 and fy23.filed_at == date(2024, 2, 15) and fy23.accession_or_url == "k-23"
    assert (
        fy23.source == "edgar_xbrl"
        and fy23.source_concept == "us-gaap:Revenues"
        and fy23.confidence == 1.0
        and fy23.approved
    )
    restated = _one(rows, "revenue", "FY", date(2023, 12, 31), restated=True)
    assert restated.value == 1010 and restated.filed_at == date(2025, 2, 15)
    fy24 = [
        r
        for r in rows
        if r.canonical_field == "revenue" and r.period_type == "FY" and r.period_end == date(2024, 12, 31)
    ]
    assert len(fy24) == 1 and fy24[0].value == 1200  # duplicate fact deduped, no restated row
    assert (
        fy24[0].fiscal_year == 2024
        and fy24[0].fiscal_period == "FY"
        and fy24[0].period_start == date(2024, 1, 1)
    )


def test_quarterly_discrete_vs_ytd_and_q4_derivation(companyfacts):
    rows = _rows(companyfacts)
    q = {
        r.period_end: r
        for r in rows
        if r.canonical_field == "revenue" and r.period_type == "Q" and not r.restated_flag
    }
    assert q[date(2024, 3, 31)].value == 250 and q[date(2024, 3, 31)].fiscal_period == "Q1"
    assert q[date(2024, 6, 30)].value == 300  # discrete 3M beats 6M YTD
    assert q[date(2024, 12, 31)].value == 330 and q[date(2024, 12, 31)].fiscal_period == "Q4"
    assert q[date(2024, 12, 31)].source_concept.startswith("derived:us-gaap:Revenues") and q[
        date(2024, 12, 31)
    ].period_start == date(2024, 10, 1)
    assert q[date(2023, 12, 31)].value == 300  # FY2023 1000 - 9M 700
    cfo = {r.period_end: r.value for r in rows if r.canonical_field == "cfo" and r.period_type == "Q"}
    assert cfo == {
        date(2024, 3, 31): 100,
        date(2024, 6, 30): 120,
        date(2024, 9, 30): 130,
        date(2024, 12, 31): 150,
    }
    eps = {r.period_end: r.value for r in rows if r.canonical_field == "eps_basic" and r.period_type == "Q"}
    assert eps[date(2024, 12, 31)] == 0.5
    assert not [r for r in rows if r.period_type == "H"]  # US filers: 6M only used for derivation


def test_instants_and_ttm(companyfacts):
    rows = _rows(companyfacts)
    assert _one(rows, "total_assets", "FY", date(2024, 12, 31)).value == 5500
    assert _one(rows, "total_assets", "Q", date(2024, 12, 31)).fiscal_period == "Q4"
    assert _one(rows, "total_assets", "Q", date(2024, 3, 31)).fiscal_period == "Q1"
    assert _one(rows, "shares_outstanding", "FY", date(2024, 12, 31)).value == 1000
    ttm = {
        r.period_end: r.value
        for r in rows
        if r.canonical_field == "revenue" and r.period_type == "TTM" and not r.restated_flag
    }
    assert ttm[date(2024, 6, 30)] == 260 + 300 + 250 + 300 and ttm[date(2024, 12, 31)] == 1200
    assert date(2023, 9, 30) not in ttm  # only 3 quarters before it
    assert _one(rows, "total_assets", "TTM", date(2024, 12, 31)).value == 5500  # stocks: latest


def test_resolve_frame_and_derived(companyfacts):
    rows = _rows(companyfacts)
    resolved = resolve(rows, period_type="FY", restated=False)
    frame = build_frame(resolved, "FY")
    assert frame["period_end"].to_list() == [date(2023, 12, 31), date(2024, 12, 31)]
    assert frame["revenue"].to_list() == [1000.0, 1200.0]
    filled, derivs = apply_derived(frame, resolved)
    fy24 = filled.filter(filled["period_end"] == date(2024, 12, 31)).to_dicts()[0]
    assert fy24["gross_profit"] == 500 and fy24["total_liabilities"] == 2500
    assert {(d.field, d.period_end) for d in derivs} >= {
        ("gross_profit", date(2024, 12, 31)),
        ("total_liabilities", date(2024, 12, 31)),
    }
    restated = build_frame(resolve(rows, period_type="FY", restated=True), "FY")
    assert restated["revenue"].to_list() == [1010.0, 1200.0]
    payload = frame_to_payload(filled, resolved, derivs)
    assert payload["periods"][1]["label"] == "FY2024" and payload["fields"]["revenue"] == [1000.0, 1200.0]
    assert payload["provenance"]["revenue"][1]["accession_or_url"] == "k-24"
    assert payload["provenance"]["gross_profit"][1]["source"] == "derived"
