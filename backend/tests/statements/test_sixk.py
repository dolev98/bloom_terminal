from datetime import date

from app.statements import service
from app.statements.concept_map import ConceptMapper, normalize_label
from app.statements.sixk import archive_urls, find_statement_tables, html_text, parse_number, tables_to_facts
from app.statements.validation import validate_period
from tests.statements.conftest import BS_ROWS, sixk_html


def test_number_and_label_normalisation():
    assert (
        parse_number("(1,234)") == -1234 and parse_number("1,234.5") == 1234.5 and parse_number("$") is None
    )
    assert (
        parse_number("—") == 0.0
        and parse_number("12%") is None
        and parse_number("Note 3") is None
        and parse_number("(50") == -50
    )
    assert (
        normalize_label("Net cash provided by (used in) operating activities (Note 3)")
        == "net cash provided by operating activities"
    )
    assert normalize_label("Selling, general and administrative*") == "selling general and administrative"
    m = ConceptMapper.from_seed()
    assert m.match_label("CF", "Net cash (used in) provided by operating activities") == ("cfo", 1)
    assert m.match_label("IS", "Financial expenses, net") == ("other_nonoperating_income", -1)
    assert m.match_label("IS", "Net income attributable to Teva") == ("net_income_to_common", 1)
    assert (
        m.match_label("BS", "Total assets") == ("total_assets", 1)
        and m.match_label("IS", "Total assets") is None
    )


def test_find_statement_tables_kinds_units_periods(sixk_document):
    tables = find_statement_tables(sixk_document)
    assert [t.kind for t in tables] == ["IS", "BS", "CF"]
    assert all(t.unit_scale == 3 and t.currency == "USD" for t in tables)
    is_ = tables[0]
    assert [(p.end, p.months) for p in is_.periods] == [(date(2024, 3, 31), 3), (date(2023, 3, 31), 3)]
    assert [(p.end, p.months) for p in tables[1].periods] == [
        (date(2024, 3, 31), None),
        (date(2023, 12, 31), None),
    ]
    rows = dict(is_.rows)
    assert rows["Revenues"] == [1500.0, 1300.0]
    basics = [v for lbl, v in is_.rows if lbl == "Basic"]
    assert basics == [[0.21, 0.16], [1000000.0, 1000000.0]]  # EPS row, then weighted shares row
    assert "Testco" in html_text(sixk_document) and "<" not in html_text(sixk_document)


def test_tables_to_facts_scale_signs_and_periods(sixk_document):
    tables = find_statement_tables(sixk_document)
    rows, meta = tables_to_facts(
        tables,
        ConceptMapper.from_seed(),
        entity_id="cik:9",
        ticker="TSTC",
        fye_month=12,
        accession="0001-24-1",
        filed_at=date(2024, 5, 2),
    )
    by = {(r.canonical_field, r.period_type, r.period_end): r for r in rows}
    rev = by[("revenue", "Q", date(2024, 3, 31))]
    assert (
        rev.value == 1_500_000
        and rev.unit_scale == 3
        and rev.source == "6k_parsed"
        and rev.confidence == 0.7
        and not rev.approved
    )
    assert (
        rev.fiscal_period == "Q1"
        and rev.fiscal_year == 2024
        and rev.period_start == date(2024, 1, 1)
        and rev.currency == "USD"
    )
    assert by[("capex", "Q", date(2024, 3, 31))].value == 50_000  # stored positive
    assert by[("debt_repaid", "Q", date(2024, 3, 31))].value == 20_000
    assert by[("sga_expense", "Q", date(2024, 3, 31))].value == 230_000  # S&M + G&A combined
    assert by[("other_nonoperating_income", "Q", date(2024, 3, 31))].value == -10_000
    assert (
        by[("eps_basic", "Q", date(2024, 3, 31))].value == 0.21
        and by[("eps_diluted", "Q", date(2023, 3, 31))].value == 0.15
    )
    assert by[("shares_basic_weighted", "Q", date(2024, 3, 31))].value == 1_000_000_000
    assert by[("net_income_to_common", "Q", date(2024, 3, 31))].value == 210_000
    assert by[("total_assets", "Q", date(2024, 3, 31))].value == 1_800_000
    assert (
        by[("total_assets", "FY", date(2023, 12, 31))].value == 1_700_000
        and by[("total_assets", "Q", date(2023, 12, 31))].fiscal_period == "Q4"
    )
    assert ("total_assets", "FY", date(2024, 3, 31)) not in by
    assert (
        by[("fx_effect", "Q", date(2024, 3, 31))].value == 0.0
        and by[("net_change_in_cash", "Q", date(2024, 3, 31))].value == 50_000
    )
    assert (
        meta["tables"][0]["matched_rows"] >= 14
        and "Total liabilities and shareholders' equity" in meta["unmatched_labels"]
    )
    vals = {k[0]: r.value for k, r in by.items() if k[1] == "Q" and k[2] == date(2024, 3, 31)}
    v = validate_period(vals)
    assert v["passed"] and {c["name"] for c in v["checks"]} == {"balance_sheet", "gross_profit", "cash_flow"}
    assert (
        archive_urls(1234, "0001-24-000001")[0]
        == "https://www.sec.gov/Archives/edgar/data/1234/000124000001/index.json"
    )


async def test_ingest_6k_validation_gate(db, sixk_document):
    async with __import__("app.data.store.sqlite", fromlist=["session_scope"]).session_scope() as s:
        from app.statements.models import Entity

        s.add(
            Entity(
                entity_id="cik:9",
                ticker="TSTC",
                name="Testco",
                filer_type="foreign_20f",
                cik=9,
                fiscal_year_end_month=12,
            )
        )
    ok = await service.ingest_6k(
        "TSTC",
        "0001-24-000001",
        filed_at=date(2024, 5, 2),
        html_docs=[("ex99-1.htm", "https://sec.test/ex99-1.htm", sixk_document)],
    )
    assert ok["status"] == "approved" and ok["source"] == "6k_parsed" and ok["doc"]["validation"]["passed"]
    st = await service.get_statements("TSTC", "Q")
    rev = dict(zip([p["period_end"] for p in st["periods"]], st["fields"]["revenue"], strict=True))
    assert rev == {
        "2023-03-31": 1_300_000.0,
        "2023-12-31": None,
        "2024-03-31": 1_500_000.0,
    }  # Dec-31 = BS-only Q4
    assert st["provenance"]["revenue"][-1]["source"] == "6k_parsed"
    assert st["fields"]["fcf"][-1] == 250_000.0 and st["provenance"]["fcf"][-1]["source"] == "derived"
    again = await service.ingest_6k(
        "TSTC", "0001-24-000001", html_docs=[("ex99-1.htm", "https://sec.test/ex99-1.htm", sixk_document)]
    )
    assert again["status"] == "exists"
    # broken balance sheet -> pending, hidden from approved-only queries until approved
    broken = [r if r[0] != "Total liabilities" else ("Total liabilities", ("400", "560")) for r in BS_ROWS]
    bad = await service.ingest_6k(
        "TSTC",
        "0001-24-000002",
        filed_at=date(2024, 8, 2),
        html_docs=[("ex99.htm", "https://sec.test/ex99-2.htm", sixk_html(broken))],
    )
    assert bad["status"] == "pending" and not bad["doc"]["validation"]["passed"]
    pending = await service.list_docs(status="pending")
    assert [d["id"] for d in pending] == [bad["doc"]["id"]]
    cov = {(c["period_type"], c["period_end"]): c for c in await service.coverage("TSTC")}
    q1 = cov[("Q", date(2024, 3, 31))]["sources"]
    assert any(s["source"] == "6k_parsed" and s["approved"] > 0 for s in q1)
    decided = await service.decide_doc(bad["doc"]["id"], approve=False)
    assert decided["status"] == "rejected" and await service.list_docs(status="pending") == []
