from datetime import date

import respx
from httpx import Response

from app.statements import service
from app.statements.concept_map import ConceptMapper
from app.statements.xbrl_org import normalize_filing, xbrl_json_to_facts


def _fact(concept, period, value, unit="iso4217:DKK", extra=None, decimals=-6):
    dims = {"concept": concept, "entity": "scheme:LEI1", "period": period}
    if unit:
        dims["unit"] = unit
    if extra:
        dims.update(extra)
    return {"value": str(value), "decimals": decimals, "dimensions": dims}


def xbrl_json() -> dict:
    return {
        "documentInfo": {"documentType": "https://xbrl.org/2021/xbrl-json"},
        "facts": {
            "f1": _fact("ifrs-full:Revenue", "2023-01-01T00:00:00/2024-01-01T00:00:00", 1_000_000),
            "f2": _fact("ifrs-full:Revenue", "2022-01-01T00:00:00/2023-01-01T00:00:00", 900_000),
            "f3": _fact("ifrs-full:Revenue", "2023-01-01T00:00:00/2023-07-01T00:00:00", 450_000),
            "f4": _fact(
                "ifrs-full:Revenue",
                "2023-01-01T00:00:00/2024-01-01T00:00:00",
                1,
                extra={"ifrs-full:SegmentsAxis": "x:Europe"},
            ),
            "f5": _fact("ifrs-full:ProfitLoss", "2023-01-01T00:00:00/2024-01-01T00:00:00", 120_000),
            "f6": _fact("ifrs-full:Assets", "2024-01-01T00:00:00", 5_000_000),
            "f7": _fact("ifrs-full:Assets", "2023-07-01T00:00:00", 4_800_000),
            "f8": _fact("ifrs-full:Equity", "2024-01-01T00:00:00", 3_000_000),
            "f9": _fact(
                "ifrs-full:CashFlowsFromUsedInOperatingActivities",
                "2023-01-01T00:00:00/2024-01-01T00:00:00",
                200_000,
            ),
            "f10": _fact(
                "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
                "2023-01-01T00:00:00/2024-01-01T00:00:00",
                50_000,
            ),
            "f11": _fact(
                "ifrs-full:BasicEarningsLossPerShare",
                "2023-01-01T00:00:00/2024-01-01T00:00:00",
                1.2,
                unit="iso4217:DKK/xbrli:shares",
                decimals=2,
            ),
            "f12": _fact(
                "ifrs-full:NameOfReportingEntityOrOtherMeansOfIdentification",
                "2024-01-01T00:00:00",
                "Co",
                unit=None,
            ),
        },
    }


def test_xbrl_json_to_facts_periods_and_half_year():
    rows = xbrl_json_to_facts(
        xbrl_json(),
        ConceptMapper.from_seed(),
        entity_id="lei:LEI1",
        ticker="NOVO",
        url="https://filings.xbrl.org/x.json",
        filed_at=date(2024, 3, 1),
    )
    by = {(r.canonical_field, r.period_type, r.period_end): r for r in rows}
    fy = by[("revenue", "FY", date(2023, 12, 31))]
    assert (
        fy.value == 1_000_000
        and fy.currency == "DKK"
        and fy.source == "xbrl_org"
        and fy.period_start == date(2023, 1, 1)
        and fy.fiscal_year == 2023
    )
    assert by[("revenue", "FY", date(2022, 12, 31))].value == 900_000
    assert (
        by[("revenue", "H", date(2023, 6, 30))].value == 450_000
        and by[("revenue", "H", date(2023, 6, 30))].fiscal_period == "H1"
    )
    assert by[("revenue", "H", date(2023, 12, 31))].value == 550_000 and by[
        ("revenue", "H", date(2023, 12, 31))
    ].source_concept.startswith("derived:")
    assert (
        by[("total_assets", "FY", date(2023, 12, 31))].value == 5_000_000
        and by[("total_assets", "H", date(2023, 6, 30))].value == 4_800_000
    )
    assert (
        by[("eps_basic", "FY", date(2023, 12, 31))].value == 1.2
        and by[("eps_basic", "FY", date(2023, 12, 31))].currency == "DKK"
    )
    assert by[("capex", "FY", date(2023, 12, 31))].value == 50_000
    assert not [r for r in rows if r.value == 1]  # segment-dimensioned fact excluded


def test_normalize_filing():
    item = {
        "id": "1",
        "type": "filing",
        "attributes": {
            "fxo_id": "LEI1-2023-12-31-ESEF-DK-0",
            "country": "DK",
            "period_end": "2023-12-31",
            "date_added": "2024-03-01T10:00:00",
            "json_url": "/LEI1/2023-12-31/ESEF/DK/0/report.json",
        },
        "relationships": {"entity": {"data": {"type": "entity", "id": "e1"}}},
    }
    inc = [{"type": "entity", "id": "e1", "attributes": {"identifier": "LEI1", "name": "Novo Test A/S"}}]
    f = normalize_filing(item, inc)
    assert (
        f["lei"] == "LEI1"
        and f["entity_name"] == "Novo Test A/S"
        and f["json_url"] == "https://filings.xbrl.org/LEI1/2023-12-31/ESEF/DK/0/report.json"
    )


@respx.mock
async def test_ingest_xbrl_org_end_to_end(db):
    listing = {
        "data": [
            {
                "id": "1",
                "type": "filing",
                "attributes": {
                    "fxo_id": "LEI1-2023-12-31-ESEF-DK-0",
                    "period_end": "2023-12-31",
                    "date_added": "2024-03-01T10:00:00",
                    "json_url": "/LEI1/2023/report.json",
                },
                "relationships": {"entity": {"data": {"type": "entity", "id": "e1"}}},
            }
        ],
        "included": [
            {"type": "entity", "id": "e1", "attributes": {"identifier": "LEI1", "name": "Novo Test A/S"}}
        ],
    }
    respx.get(url__regex=r"https://filings\.xbrl\.org/api/filings.*").mock(
        return_value=Response(200, json=listing)
    )
    respx.get("https://filings.xbrl.org/LEI1/2023/report.json").mock(
        return_value=Response(200, json=xbrl_json())
    )
    res = await service.ingest_xbrl_org("Novo Test", ticker="NOVO")
    assert res["status"] == "ok" and res["entity_id"] == "lei:LEI1" and res["inserted"] > 5
    ent = await service.get_entity("NOVO")
    assert (
        ent.filer_type == "ifrs_esef"
        and ent.currency == "DKK"
        and service.capabilities(ent)["xbrl_half_year"]
    )
    st = await service.get_statements("NOVO", "FY")
    assert st["fields"]["revenue"] == [900_000.0, 1_000_000.0] and st["fields"]["fcf"][1] == 150_000.0
    assert (
        st["fields"]["total_liabilities"][1] == 2_000_000.0
        and st["provenance"]["total_liabilities"][1]["source"] == "derived"
    )
    h = await service.get_statements("NOVO", "H")
    assert [p["fiscal_period"] for p in h["periods"]] == ["H1", "H2"]
