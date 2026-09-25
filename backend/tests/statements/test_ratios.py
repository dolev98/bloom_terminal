from datetime import date

import polars as pl
import pytest

from app.statements.fields import ALL_FIELDS
from app.statements.ratios import compute_ratios, effective_tax_rate, piotroski

FY24 = {
    "revenue": 1000.0,
    "cost_of_revenue": 600.0,
    "gross_profit": 400.0,
    "operating_income": 200.0,
    "depreciation_amortization": 30.0,
    "pretax_income": 190.0,
    "income_tax_expense": 40.0,
    "net_income": 150.0,
    "interest_expense": 20.0,
    "stock_based_comp": 25.0,
    "cfo": 220.0,
    "capex": 50.0,
    "total_assets": 2000.0,
    "total_liabilities": 1000.0,
    "total_equity": 1000.0,
    "total_current_assets": 800.0,
    "total_current_liabilities": 400.0,
    "retained_earnings": 500.0,
    "long_term_debt": 300.0,
    "cash_and_equivalents": 200.0,
    "accounts_receivable": 150.0,
    "inventory": 100.0,
    "accounts_payable": 120.0,
    "shares_outstanding": 100.0,
    "eps_diluted": 1.5,
}
FY23 = {
    "revenue": 900.0,
    "cost_of_revenue": 560.0,
    "gross_profit": 340.0,
    "operating_income": 170.0,
    "pretax_income": 160.0,
    "income_tax_expense": 35.0,
    "net_income": 120.0,
    "cfo": 180.0,
    "capex": 40.0,
    "total_assets": 1900.0,
    "total_liabilities": 1000.0,
    "total_equity": 900.0,
    "total_current_assets": 700.0,
    "total_current_liabilities": 400.0,
    "retained_earnings": 400.0,
    "long_term_debt": 350.0,
    "cash_and_equivalents": 150.0,
    "shares_outstanding": 105.0,
    "eps_diluted": 1.2,
}


def _frame(rows: list[tuple[date, dict]], ptype="FY") -> pl.DataFrame:
    data = {
        "period_end": [d for d, _ in rows],
        "period_type": [ptype] * len(rows),
        "fiscal_year": [d.year for d, _ in rows],
        "fiscal_period": ["FY"] * len(rows),
        "period_start": [date(d.year, 1, 1) for d, _ in rows],
        "currency": ["USD"] * len(rows),
    }
    for f in ALL_FIELDS:
        data[f] = [v.get(f) for _, v in rows]
    return pl.DataFrame(
        data,
        schema={
            "period_end": pl.Date,
            "period_type": pl.Utf8,
            "fiscal_year": pl.Int64,
            "fiscal_period": pl.Utf8,
            "period_start": pl.Date,
            "currency": pl.Utf8,
            **{f: pl.Float64 for f in ALL_FIELDS},
        },
    )


def test_margins_returns_and_leverage():
    out = compute_ratios(_frame([(date(2023, 12, 31), FY23), (date(2024, 12, 31), FY24)]), "FY")
    r = out[1]
    assert r["label"] == "FY2024"
    assert (
        r["gross_margin"] == pytest.approx(0.4)
        and r["operating_margin"] == pytest.approx(0.2)
        and r["net_margin"] == pytest.approx(0.15)
    )
    assert (
        r["ebitda_margin"] == pytest.approx(0.23)
        and r["fcf"] == 170
        and r["fcf_margin"] == pytest.approx(0.17)
    )
    assert r["roe"] == pytest.approx(150 / 950) and r["roa"] == pytest.approx(150 / 1950)
    assert r["effective_tax_rate"] == pytest.approx(40 / 190)
    nopat = 200 * (1 - 40 / 190)
    assert r["roic"] == pytest.approx(nopat / 1100)  # invested capital 1100 both years
    assert (
        r["fcf_conversion"] == pytest.approx(170 / 150)
        and r["capex_intensity"] == pytest.approx(0.05)
        and r["sbc_pct_revenue"] == pytest.approx(0.025)
    )
    assert (
        r["net_debt"] == 100
        and r["net_debt_to_ebitda"] == pytest.approx(100 / 230)
        and r["debt_to_equity"] == pytest.approx(0.3)
    )
    assert r["interest_coverage"] == 10 and r["working_capital"] == 400 and r["current_ratio"] == 2.0
    assert (
        r["quick_ratio"] == pytest.approx(350 / 400)
        and r["dso"] == pytest.approx(54.75)
        and r["dio"] == pytest.approx(100 / 600 * 365)
    )
    assert r["dpo"] == pytest.approx(120 / 600 * 365) and r["asset_turnover"] == pytest.approx(1000 / 1950)
    assert (
        r["revenue_yoy"] == pytest.approx(1 / 9)
        and r["net_income_yoy"] == pytest.approx(0.25)
        and r["eps_yoy"] == pytest.approx(0.25)
    )
    first = out[0]
    assert (
        first["piotroski_f"] is None
        and first["revenue_yoy"] is None
        and first["roe"] == pytest.approx(120 / 900)
    )


def test_altman_and_piotroski_known_case():
    out = compute_ratios(
        _frame([(date(2023, 12, 31), FY23), (date(2024, 12, 31), FY24)]), "FY", market_cap=3000.0
    )
    r = out[1]
    assert r["altman_z2"] == pytest.approx(6.56 * 0.2 + 3.26 * 0.25 + 6.72 * 0.1 + 1.05 * 1.0)
    assert r["altman_z"] == pytest.approx(1.2 * 0.2 + 1.4 * 0.25 + 3.3 * 0.1 + 0.6 * 3.0 + 1.0 * 0.5)
    assert r["piotroski_f"] == 9
    worse = {**FY24, "net_income": -10.0, "cfo": -5.0, "shares_outstanding": 110.0, "long_term_debt": 400.0}
    assert piotroski(worse, FY23) == 4  # accrual (cfo > ni), current ratio, gross margin, asset turnover
    assert compute_ratios(_frame([(date(2024, 12, 31), FY24)]), "FY")[0]["altman_z"] is None


def test_quarterly_annualisation_and_tax_clamp():
    q = {
        **FY24,
        "revenue": 250.0,
        "net_income": 40.0,
        "operating_income": 50.0,
        "cfo": 60.0,
        "capex": 10.0,
        "accounts_receivable": 150.0,
    }
    rows = [
        (date(2024, 3, 31), q),
        (date(2024, 6, 30), q),
        (date(2024, 9, 30), q),
        (date(2024, 12, 31), q),
        (date(2025, 3, 31), {**q, "revenue": 275.0}),
    ]
    out = compute_ratios(_frame(rows, "Q"), "Q")
    last = out[-1]
    assert last["roe"] == pytest.approx(40 * 4 / 1000) and last["dso"] == pytest.approx(150 / 275 * 91)
    assert last["revenue_yoy"] == pytest.approx(0.1)  # vs 4 quarters back
    assert (
        effective_tax_rate(100, 200) == 0.35
        and effective_tax_rate(10, -5) == 0.21
        and effective_tax_rate(None, 5) == 0.21
    )
    assert compute_ratios(_frame([]), "FY") == []
