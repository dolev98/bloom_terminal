from datetime import date

from app.statements import pdf_pipeline, service
from app.statements.schemas import StatementsExtraction
from tests.statements.conftest import extraction_payload, fake_parse_factory


def test_locate_hebrew_headings_and_units():
    texts = [
        "תוכן העניינים\nדוח על המצב הכספי 3\nדוח רווח והפסד 4",
        'דוחות מאוחדים על המצב הכספי\nבאלפי ש"ח\n'
        + "\n".join(f"סעיף {i} 1,{200 + i:03d} 1,{100 + i:03d}" for i in range(14)),
        "\n".join(line[::-1] for line in ("דוח מאוחד על הרווח הכולל", 'באלפי ש"ח'))
        + "\n"
        + "\n".join(f"{i} 12,{300 + i:03d} 11,{200 + i:03d}" for i in range(14)),
        "המשך\n" + "\n".join(f"{i} 5,{100 + i:03d} 4,{100 + i:03d}" for i in range(14)),
        "דוח על תזרימי המזומנים\n"
        + "\n".join(f"שורה {i} 7,{100 + i:03d} 6,{100 + i:03d}" for i in range(14)),
        "דוח רואה החשבון המבקר: ביקרנו את הדוח על המצב הכספי, דוח רווח והפסד ודוח על תזרימי המזומנים.",
    ]
    loc = pdf_pipeline.locate_statement_pages(texts)
    assert loc.toc_pages == [0, 5] and loc.pages == [1, 2, 3, 4]
    assert loc.kinds[1] == ["BS"] and loc.kinds[2] == ["IS"] and loc.kinds[4] == ["CF"]
    assert loc.unit_scale == 3 and loc.currency == "ILS"
    assert pdf_pipeline.detect_units("במיליוני ש״ח") == (6, "ILS") and pdf_pipeline.detect_units(
        "nothing"
    ) == (None, None)


def test_real_pdf_locate_and_trim(pdf_path):
    texts = pdf_pipeline.page_texts(pdf_path)
    assert len(texts) == 5 and "financial position" in texts[1].lower()
    loc = pdf_pipeline.locate_statement_pages(texts)
    assert loc.pages == [1, 2, 3] and loc.unit_scale == 3 and loc.currency == "ILS" and 0 in loc.toc_pages
    trimmed = pdf_pipeline.build_trimmed_pdf(pdf_path, loc.pages)
    from io import BytesIO

    from pypdf import PdfReader

    assert len(PdfReader(BytesIO(trimmed)).pages) == 3


async def test_extract_with_fake_parse(pdf_path):
    calls: list = []
    res = await pdf_pipeline.extract(
        pdf_path,
        ticker="TST.TA",
        fiscal_year=2024,
        period_type="FY",
        parse_fn=fake_parse_factory(extraction_payload(), calls),
    )
    assert isinstance(res["extraction"], StatementsExtraction) and res["page_map"] == {1: 2, 2: 3, 3: 4}
    assert calls[0]["schema"] == "StatementsExtraction" and calls[0]["content_types"] == ["document", "text"]
    assert calls[0]["purpose"] == "statements_pdf" and calls[0]["model"] == "claude-sonnet-5"
    rows = pdf_pipeline.extraction_to_facts(
        res["extraction"],
        entity_id="tase:TST",
        ticker="TST.TA",
        path_or_url=str(pdf_path),
        doc_id=7,
        page_map=res["page_map"],
    )
    by = {(r.canonical_field, r.period_end): r for r in rows}
    rev = by[("revenue", date(2024, 12, 31))]
    assert (
        rev.value == 2_000_000
        and rev.currency == "ILS"
        and rev.unit_scale == 3
        and rev.page == 2
        and rev.doc_id == 7
    )
    assert (
        rev.confidence == 0.85
        and not rev.approved
        and rev.source == "pdf_llm"
        and rev.period_start == date(2024, 1, 1)
    )
    assert (
        by[("eps_basic", date(2024, 12, 31))].value == 2.2
        and by[("shares_basic_weighted", date(2024, 12, 31))].value == 100_000_000
    )
    assert (
        by[("capex", date(2024, 12, 31))].value == 80_000
        and by[("cfi", date(2024, 12, 31))].value == -120_000
    )


async def test_ingest_pdf_pending_then_approve(db, pdf_path):
    res = await service.ingest_pdf(
        pdf_path, "TST.TA", 2024, "FY", parse_fn=fake_parse_factory(extraction_payload())
    )
    assert res["status"] == "pending" and res["rows"] > 10
    doc = res["doc"]
    assert (
        doc["validation"]["passed"]
        and doc["validation"]["pages"] == [2, 3, 4]
        and doc["llm_usage"]["cost_usd"] == 0.005
    )
    names = {c["name"] for p in doc["validation"]["periods"] for c in p["checks"]}
    assert names == {"balance_sheet", "gross_profit", "cash_flow"}
    ent = await service.get_entity("TST.TA")
    assert (
        ent.filer_type == "tase_only"
        and ent.entity_id == "tase:TST"
        and service.capabilities(ent)
        == {
            "xbrl_annual": False,
            "xbrl_quarterly": False,
            "xbrl_half_year": False,
            "sixk_parsed": False,
            "pdf_llm": True,
        }
    )
    hidden = await service.get_statements("TST.TA", "FY")
    assert hidden["periods"] == []
    shown = await service.get_statements("TST.TA", "FY", approved_only=False)
    assert shown["fields"]["revenue"] == [1_800_000.0, 2_000_000.0] and shown["fields"]["fcf"] == [
        None,
        270_000.0,
    ]
    approved = await service.decide_doc(doc["id"], approve=True)
    assert approved["status"] == "approved"
    st = await service.get_statements("TST.TA", "FY")
    assert (
        st["fields"]["revenue"] == [1_800_000.0, 2_000_000.0] and st["provenance"]["revenue"][1]["page"] == 2
    )
    assert st["provenance"]["fcf"][1]["source"] == "derived" and st["provenance"]["fcf"][1]["approved"]
    r = await service.ratios("TST.TA", "FY")
    assert (
        r["periods"][1]["gross_margin"] == 0.4 and r["periods"][1]["altman_z2"] is None
    )  # no retained earnings
    # a second upload whose comparative disagrees with approved facts fails the comparatives check
    bad = extraction_payload()
    bad["periods"][1]["values"]["revenue"] = 1500.0
    res2 = await service.ingest_pdf(pdf_path, "TST.TA", 2024, "FY", parse_fn=fake_parse_factory(bad))
    checks = [
        c for p in res2["doc"]["validation"]["periods"] for c in p["checks"] if c["name"] == "comparatives"
    ]
    assert any(not c["ok"] for c in checks) and not res2["doc"]["validation"]["passed"]


async def test_ingest_pdf_failure_is_recorded(db, pdf_path):
    async def boom(*a, **k):
        raise RuntimeError("LLM down")

    res = await service.ingest_pdf(pdf_path, "TST.TA", 2024, "FY", parse_fn=boom)
    assert res["status"] == "failed" and "LLM down" in res["error"]
    assert (await service.list_docs(status="failed"))[0]["validation"]["error"].startswith("RuntimeError")
