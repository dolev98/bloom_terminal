import io
from datetime import date, datetime

import openpyxl
import respx
from httpx import Response

from app.valuation.providers import damodaran, treasury

XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
<entry><content type="application/xml"><m:properties><d:Id m:type="Edm.Int32">1</d:Id><d:NEW_DATE m:type="Edm.DateTime">2026-09-22T00:00:00</d:NEW_DATE><d:BC_2YEAR m:type="Edm.Double">3.55</d:BC_2YEAR><d:BC_10YEAR m:type="Edm.Double">4.12</d:BC_10YEAR></m:properties></content></entry>
<entry><content type="application/xml"><m:properties><d:Id m:type="Edm.Int32">2</d:Id><d:NEW_DATE m:type="Edm.DateTime">2026-09-23T00:00:00</d:NEW_DATE><d:BC_2YEAR m:type="Edm.Double">3.50</d:BC_2YEAR><d:BC_10YEAR m:type="Edm.Double">4.20</d:BC_10YEAR></m:properties></content></entry>
</feed>"""


def test_parse_treasury_curve():
    rows = treasury.parse_curve_xml(XML)
    assert [r["date"] for r in rows] == [date(2026, 9, 22), date(2026, 9, 23)] and rows[-1]["10y"] == 4.20


@respx.mock
async def test_get_rf_fetches_then_caches(db):
    route = respx.get(url__regex=r"https://home\.treasury\.gov/.*field_tdr_date_value=\d{4}").mock(
        return_value=Response(200, text=XML)
    )
    rf = await treasury.get_rf()
    assert abs(rf["value"] - 0.042) < 1e-9 and rf["as_of"] == "2026-09-23" and rf["source"] == "treasury"
    again = await treasury.get_rf()
    assert again["cached"] is True and route.call_count == 1


@respx.mock
async def test_get_rf_falls_back_to_fred_series(db):
    import polars as pl

    from app.data.series_service import write_manual

    respx.get(url__regex=r"https://home\.treasury\.gov/.*").mock(return_value=Response(500))
    write_manual(
        "fred:DGS10",
        pl.DataFrame({"ts": [datetime(2026, 9, 20)], "value": [4.5]}).with_columns(
            pl.col("ts").cast(pl.Datetime("us"))
        ),
    )
    rf = await treasury.get_rf()
    assert abs(rf["value"] - 0.045) < 1e-9 and rf["source"] == "fred:DGS10"


def test_parse_implied_erp():
    html = "<html><body><p>Implied ERP on September 1, 2026 = 4.33% (Trailing 12 month cash yield)</p></body></html>"
    assert damodaran.parse_implied_erp(html) == (0.0433, date(2026, 9, 1))
    assert damodaran.parse_implied_erp("nothing here") is None


def _xlsx(sheet: str, rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@respx.mock
async def test_crp_and_industry_from_discovered_links(db):
    html = '<a href="../pc/datasets/ctryprem26.xlsx">Country premiums</a> <a href="../pc/datasets/betas.xlsx">Betas by industry</a>'
    respx.get(damodaran.DATA_URL).mock(return_value=Response(200, text=html))
    ctry = _xlsx(
        "ERPs by country",
        [
            ["Country risk premiums", None, None, None],
            [None],
            ["Country", "Rating", "Default Spread", "Equity Risk Premium", "Country Risk Premium"],
            ["United States", "Aaa", 0.0, 4.33, 0.0],
            ["Israel", "Baa1", 1.95, 6.40, 2.07],
        ],
    )
    respx.get("https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctryprem26.xlsx").mock(
        return_value=Response(200, content=ctry)
    )
    crp = await damodaran.get_crp("Israel")
    assert abs(crp["value"] - 0.0207) < 1e-9 and crp["as_of"] == "2026-01-01" and "Baa1" in crp["note"]
    betas = _xlsx(
        "Industry Averages",
        [
            [
                "Industry Name",
                "Number of firms",
                "Beta",
                "D/E Ratio",
                "Unlevered beta",
                "Unlevered beta corrected for cash",
            ],
            ["Software (System & Application)", 300, 1.25, 0.05, 1.20, 1.28],
            ["Total Market", 6000, 1.0, 0.3, 0.8, 0.85],
        ],
    )
    respx.get("https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xlsx").mock(
        return_value=Response(200, content=betas)
    )
    out = await damodaran.refresh_industry_stats(kinds=("betas",))
    assert out["betas"] == 1
    rows = await damodaran.industry_stats("betas")
    assert (
        rows[0]["industry"] == "Software (System & Application)"
        and rows[0]["values"]["unlevered_beta"] == 1.20
    )
    from app.valuation.service import set_peers

    await set_peers("MSFT", industry="Software (System & Application)")
    assert await damodaran.industry_beta_for("MSFT") == 1.20
    snap = await damodaran.macro_snapshot()
    assert "crp_israel" in snap["macro"] and snap["industry_stats_as_of"]["betas"] == "2026-01-01"
