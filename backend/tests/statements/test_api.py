"""API tests via the real app (`client` fixture) with sec.gov mocked by respx."""

import respx
from httpx import Response

from tests.statements.conftest import PDF_PAGES, extraction_payload, fake_parse_factory, make_pdf

TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 818686, "ticker": "TEVA", "title": "Teva Pharmaceutical"},
}


def _submissions(
    cik: int, name: str, forms: list[str], accessions: list[str] | None = None, fye="1231"
) -> dict:
    accs = accessions or [f"0001-24-{i:06d}" for i in range(len(forms))]
    return {
        "cik": str(cik),
        "name": name,
        "fiscalYearEnd": fye,
        "filings": {
            "recent": {
                "form": forms,
                "accessionNumber": accs,
                "filingDate": ["2024-05-01"] * len(forms),
                "reportDate": ["2024-03-31"] * len(forms),
                "primaryDocument": ["doc.htm"] * len(forms),
                "items": [""] * len(forms),
            }
        },
    }


def _mock_sec(companyfacts: dict, sixk_document: str | None = None):
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(return_value=Response(200, json=TICKERS))
    respx.get("https://data.sec.gov/submissions/CIK0000320193.json").mock(
        return_value=Response(200, json=_submissions(320193, "Apple Inc.", ["10-K", "10-Q", "8-K"]))
    )
    respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json").mock(
        return_value=Response(200, json=companyfacts)
    )
    annual = [
        f
        for f in companyfacts["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
        if f.get("start", "").endswith("-01-01") and f["end"].endswith("-12-31")
    ]  # 20-F filers: annual XBRL only
    teva_facts = {"cik": 818686, "facts": {"us-gaap": {"Revenues": {"units": {"USD": annual}}}}}
    respx.get("https://data.sec.gov/submissions/CIK0000818686.json").mock(
        return_value=Response(
            200,
            json=_submissions(
                818686, "Teva", ["6-K", "20-F", "6-K"], ["0001-24-000001", "0001-24-000002", "0001-24-000003"]
            ),
        )
    )
    respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000818686.json").mock(
        return_value=Response(200, json=teva_facts)
    )
    if sixk_document is not None:
        index = {
            "directory": {
                "item": [
                    {"name": "zk123.htm"},
                    {"name": "exhibit_99-1.htm"},
                    {"name": "0001-24-000001-index.htm"},
                ]
            }
        }
        respx.get(url__regex=r"https://www\.sec\.gov/Archives/edgar/data/818686/\d+/index\.json").mock(
            return_value=Response(200, json=index)
        )
        respx.get(url__regex=r"https://www\.sec\.gov/Archives/edgar/data/818686/\d+/exhibit_99-1\.htm").mock(
            return_value=Response(200, text=sixk_document)
        )


@respx.mock
def test_us_filer_refresh_statements_ratios_coverage(client, companyfacts):
    _mock_sec(companyfacts)
    assert client.get("/api/statements/AAPL").status_code == 404
    r = client.post("/api/statements/aapl/refresh")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["edgar"]["status"] == "ok" and body["edgar"]["inserted"] > 20 and "sixk" not in body
    ent = client.get("/api/statements/AAPL/entity").json()
    assert (
        ent["entity_id"] == "cik:320193"
        and ent["filer_type"] == "us_10k"
        and ent["fiscal_year_end_month"] == 12
    )
    assert ent["capabilities"] == {
        "xbrl_annual": True,
        "xbrl_quarterly": True,
        "xbrl_half_year": False,
        "sixk_parsed": False,
        "pdf_llm": True,
    }
    fy = client.get("/api/statements/AAPL", params={"period": "FY"}).json()
    assert [p["label"] for p in fy["periods"]] == ["FY2023", "FY2024"] and fy["fields"]["revenue"] == [
        1010.0,
        1200.0,
    ]
    assert (
        fy["fields"]["gross_profit"] == [410.0, 500.0]
        and fy["provenance"]["gross_profit"][1]["source"] == "derived"
    )
    pit = client.get("/api/statements/AAPL", params={"period": "FY", "restated": "false"}).json()
    assert pit["fields"]["revenue"] == [1000.0, 1200.0]
    q = client.get("/api/statements/AAPL", params={"period": "Q"}).json()
    assert q["fields"]["cfo"][-1] == 150.0 and q["periods"][-1]["fiscal_period"] == "Q4"
    ttm = client.get("/api/statements/AAPL", params={"period": "TTM"}).json()
    assert ttm["fields"]["revenue"][-1] == 1200.0
    ratios = client.get("/api/statements/AAPL/ratios", params={"period": "FY"}).json()
    assert ratios["periods"][-1]["gross_margin"] == 500 / 1200 and ratios["periods"][-1]["label"] == "FY2024"
    cov = client.get("/api/statements/AAPL/coverage").json()
    assert cov[0]["period_end"] == "2024-12-31" and {s["source"] for c in cov for s in c["sources"]} == {
        "edgar_xbrl",
        "derived",
    }
    fy_row = next(c for c in cov if c["period_type"] == "FY" and c["period_end"] == "2023-12-31")
    assert next(s for s in fy_row["sources"] if s["source"] == "edgar_xbrl")["restated"] is True
    assert client.get("/api/statements/AAPL/coverage").status_code == 200
    # second refresh is idempotent (updates, no new inserts); full=true wipes and re-inserts
    again = client.post("/api/statements/AAPL/refresh").json()["edgar"]
    assert again["inserted"] == 0 and again["updated"] > 20
    assert (
        client.post("/api/statements/AAPL/refresh", params={"full": "true"}).json()["edgar"]["inserted"] > 20
    )


@respx.mock
def test_foreign_filer_refresh_parses_6k(client, companyfacts, sixk_document):
    _mock_sec(companyfacts, sixk_document)
    r = client.post("/api/statements/TEVA/refresh", params={"limit": "2"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["edgar"]["status"] == "ok"
    assert [s["status"] for s in body["sixk"]] == ["approved", "approved"], body["sixk"]
    assert all(s["source"] == "6k_parsed" for s in body["sixk"])
    ent = client.get("/api/statements/TEVA/entity").json()
    assert (
        ent["filer_type"] == "foreign_20f"
        and ent["capabilities"]["sixk_parsed"]
        and not ent["capabilities"]["xbrl_quarterly"]
    )
    q = client.get("/api/statements/TEVA", params={"period": "Q"}).json()
    rev = dict(zip([p["period_end"] for p in q["periods"]], q["fields"]["revenue"], strict=True))
    assert rev["2024-03-31"] == 1_500_000.0 and q["provenance"]["revenue"][-1]["source"] == "6k_parsed"
    assert client.get("/api/statements/docs", params={"status": "pending"}).json() == []
    approved = client.get("/api/statements/docs", params={"status": "approved", "ticker": "TEVA"}).json()
    assert len(approved) == 2 and approved[0]["kind"] == "6k"
    # quarterly XBRL revenue from companyfacts is still there for the FY view (annual-only for 20-F filers is a data property, not enforced)
    fy = client.get("/api/statements/TEVA", params={"period": "FY"}).json()
    assert fy["fields"]["revenue"][-1] == 1200.0


def test_pdf_upload_docs_and_approval(client, tmp_path, monkeypatch):
    from app.llm import client as llm

    monkeypatch.setattr(llm, "parse", fake_parse_factory(extraction_payload()))
    pdf = make_pdf(tmp_path / "up.pdf", PDF_PAGES)
    with pdf.open("rb") as f:
        r = client.post(
            "/api/statements/TST.TA/pdf",
            files={"file": ("up.pdf", f, "application/pdf")},
            data={"fiscal_year": "2024", "period_type": "FY"},
        )
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["status"] == "pending" and up["doc_id"] and up["validation"]["passed"]
    assert (
        client.post(
            "/api/statements/TST.TA/pdf",
            files={"file": ("x.pdf", b"not a pdf", "application/pdf")},
            data={"period_type": "FY"},
        ).status_code
        == 400
    )
    pending = client.get("/api/statements/docs").json()
    assert [d["id"] for d in pending] == [up["doc_id"]] and pending[0]["source"] == "pdf_llm"
    assert client.get("/api/statements/TST.TA", params={"period": "FY"}).json()["periods"] == []
    assert client.post(f"/api/statements/docs/{up['doc_id']}/approve").json()["status"] == "approved"
    st = client.get("/api/statements/TST.TA", params={"period": "FY"}).json()
    assert st["fields"]["revenue"] == [1_800_000.0, 2_000_000.0] and st["currency"] == "ILS"
    assert client.post("/api/statements/docs/999/reject").status_code == 404
    ent = client.get("/api/statements/TST.TA/entity").json()
    assert ent["filer_type"] == "tase_only" and ent["capabilities"]["pdf_llm"]


@respx.mock
def test_concept_map_get_put_and_recompute(client, companyfacts):
    _mock_sec(companyfacts)
    rows = client.get("/api/statements/concept-map", params={"taxonomy": "us-gaap"}).json()
    assert any(r["source_concept"] == "Revenues" and r["canonical_field"] == "revenue" for r in rows)
    assert client.get("/api/statements/concept-map", params={"taxonomy": "label"}).json()
    assert client.get("/api/statements/concept-map", params={"taxonomy": "ifrs-full"}).json()
    client.post("/api/statements/AAPL/refresh")
    bad = client.put(
        "/api/statements/concept-map",
        json={"source_taxonomy": "us-gaap", "source_concept": "X", "canonical_field": "nope"},
    )
    assert bad.status_code == 400
    # map CostOfRevenue to other_operating_expense for this entity only, then recompute -> gross_profit no longer derivable
    r = client.put(
        "/api/statements/concept-map",
        json={
            "source_taxonomy": "us-gaap",
            "source_concept": "CostOfRevenue",
            "canonical_field": "cost_of_revenue",
            "priority": 99,
            "sign": 1,
            "entity_override": "cik:320193",
            "recompute_ticker": "AAPL",
        },
    )
    assert (
        r.status_code == 200
        and r.json()["row"]["entity_override"] == "cik:320193"
        and r.json()["recomputed"]["status"] == "ok"
    )
    rows2 = client.get("/api/statements/concept-map", params={"taxonomy": "us-gaap"}).json()
    assert sum(1 for x in rows2 if x["source_concept"] == "CostOfRevenue") == 2
