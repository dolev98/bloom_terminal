from datetime import date, datetime

import pytest

from app.valuation.inputs import Consensus, InputsSnapshot, MacroData, MarketData, StatementPeriod


def synthetic_inputs(
    price: float = 20.0,
    years: int = 5,
    revenue0: float = 1000.0,
    growth: float = 0.08,
    margin: float = 0.20,
    with_consensus: bool = False,
    ticker: str = "TEST",
) -> InputsSnapshot:
    """Small, internally consistent history: revenue grows `growth`/yr, constant margins, simple balance sheet."""
    annual = []
    rev = revenue0 / (1 + growth) ** (years - 1)
    for i in range(years):
        ebit = rev * margin
        pretax = ebit - 10.0
        annual.append(
            StatementPeriod(
                period_end=date(2020 + i, 12, 31),
                fiscal_year=2020 + i,
                fiscal_period="FY",
                fields={
                    "revenue": rev,
                    "operating_income": ebit,
                    "depreciation_amortization": rev * 0.05,
                    "capex": rev * 0.07,
                    "stock_based_comp": 0.0,
                    "interest_expense": 10.0,
                    "pretax_income": pretax,
                    "income_tax_expense": pretax * 0.25,
                    "net_income": pretax * 0.75,
                    "eps_diluted": pretax * 0.75 / 100.0,
                    "shares_diluted_weighted": 100.0,
                    "shares_outstanding": 100.0,
                    "cash_and_equivalents": 100.0,
                    "short_term_investments": 0.0,
                    "total_current_assets": rev * 0.3,
                    "total_current_liabilities": rev * 0.2,
                    "short_term_debt": 0.0,
                    "long_term_debt": 200.0,
                    "long_term_lease_liabilities": 0.0,
                    "minority_interest": 0.0,
                    "total_equity": 800.0,
                    "total_assets": 1500.0,
                },
            )
        )
        rev *= 1 + growth
    return InputsSnapshot(
        ticker=ticker,
        as_of=date(2026, 9, 23),
        currency="USD",
        annual=annual,
        market=MarketData(
            price=price,
            price_source="finnhub",
            price_ts=datetime(2026, 9, 23, 15, 0),
            shares_outstanding=100.0,
            market_cap=price * 100.0,
            beta_candidates={"own": 1.1},
            high_52w=price * 1.3,
            low_52w=price * 0.7,
        ),
        macro=MacroData(rf=0.04, erp=0.045, crp=0.0, tax_marginal=0.21),
        consensus=Consensus(
            revenue_growth_5y=0.08,
            target_mean=price * 1.2,
            target_low=price * 0.9,
            target_high=price * 1.5,
            source="test",
        )
        if with_consensus
        else None,
    )


@pytest.fixture
def inputs() -> InputsSnapshot:
    return synthetic_inputs()


@pytest.fixture
def vclient(client):
    """`client` + the M4 routers (a no-op once app/main.py includes them)."""
    from app.main import app
    from tests.valuation.api_fixture import ensure_routers

    ensure_routers(app)
    return client
