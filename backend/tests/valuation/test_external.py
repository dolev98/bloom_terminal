import io

import openpyxl
import respx
from httpx import Response

from app.valuation.assumptions import Assumptions
from app.valuation.models.external import (
    ExternalModel,
    load_external,
    parse_rows,
    rows_from_csv,
    rows_from_xlsx,
    sheets_csv_url,
)
from tests.valuation.conftest import synthetic_inputs

CSV_FV = "key,value,unit,scenario,note\nfair_value,42.5,USD,base,my spreadsheet\nfair_value_low,35,,base,\nfair_value_high,50,,base,\nfair_value,30,,bear,\n"
CSV_ASSUMPTIONS = "key,value,unit,scenario,note\nwacc.beta,1.2,,,\nterminal.g,2.5,%,,\nrevenue_growth,12%,,,\ntarget_ebit_margin,0.22,,,\nbridge.shares,100,,,\nwacc,9,%,,alias\n"


def test_csv_fair_value_import_and_model():
    imp = parse_rows(rows_from_csv(CSV_FV), "test.csv")
    assert set(imp.scenarios) == {"base", "bear"} and not imp.errors
    base = imp.assumptions["base"]
    assert base.fair_value == 42.5 and base.fair_value_low == 35 and base.fair_value_high == 50
    res = ExternalModel().run(synthetic_inputs(price=30.0), base)
    assert (
        res.value_per_share == 42.5
        and res.low == 35
        and res.high == 50
        and abs(res.upside(30.0) - (42.5 / 30 - 1)) < 1e-9
    )


def test_csv_assumptions_import_runs_fcff():
    imp = parse_rows(rows_from_csv(CSV_ASSUMPTIONS), "a.csv")
    a = imp.assumptions["base"]
    assert a.wacc.beta == 1.2 and abs(a.terminal.g - 0.025) < 1e-9 and abs(a.revenue_growth - 0.12) < 1e-9
    assert a.target_ebit_margin == 0.22 and a.bridge.shares == 100 and abs(a.wacc.wacc - 0.09) < 1e-9
    res = ExternalModel().run(synthetic_inputs(), a)
    assert res.model_id == "external" and res.value_per_share and res.value_per_share > 0
    assert res.diagnostics["mode"].startswith("assumptions")


def test_xlsx_rows_and_sheets_url():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["key", "value", "unit", "scenario", "note"])
    ws.append(["fair_value", 55, "", "", ""])
    ws.append(["wacc.beta", 0.9, "", "bull", ""])
    buf = io.BytesIO()
    wb.save(buf)
    rows = rows_from_xlsx(buf.getvalue())
    imp = parse_rows(rows)
    assert imp.assumptions["base"].fair_value == 55 and imp.assumptions["bull"].wacc.beta == 0.9
    assert (
        sheets_csv_url("https://docs.google.com/spreadsheets/d/abc123/edit#gid=77")
        == "https://docs.google.com/spreadsheets/d/abc123/export?format=csv&gid=77"
    )


@respx.mock
async def test_sheets_csv_url_fetch():
    respx.get("https://docs.google.com/spreadsheets/d/abc123/export?format=csv").mock(
        return_value=Response(200, text=CSV_FV)
    )
    imp = await load_external("https://docs.google.com/spreadsheets/d/abc123/edit", "sheets")
    assert imp.assumptions["base"].fair_value == 42.5


def test_assumptions_diff_and_paths():
    a = Assumptions().with_path("wacc.beta", 1.0).with_path("terminal.g", 0.02)
    b = a.with_path("wacc.beta", 1.3)
    assert a.diff(b) == {"wacc.beta": {"from": 1.0, "to": 1.3}}
    assert b.get_path("terminal.g") == 0.02 and a.checksum() != b.checksum()
