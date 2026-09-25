from app.valuation.assumptions import Assumptions
from app.valuation.models.fcff import FCFFModel
from app.valuation.models.reverse_dcf import ReverseDCFModel
from tests.valuation.conftest import synthetic_inputs

BASE = dict(
    target_ebit_margin=0.20,
    tax_rate=0.25,
    tax_rate_marginal=0.25,
    capex_pct_revenue=0.07,
    da_pct_revenue=0.05,
    nwc_pct_revenue=0.10,
    sbc_pct_revenue=0.0,
    wacc={"wacc": 0.09},
    terminal={"g": 0.02},
    bridge={"debt": 200.0, "cash": 100.0, "shares": 100.0},
)


def test_reverse_dcf_recovers_growth_that_produced_the_price():
    inputs0 = synthetic_inputs()
    g_true = 0.12
    price = FCFFModel().run(inputs0, Assumptions(revenue_growth=g_true, **BASE)).value_per_share
    inputs = inputs0.model_copy(update={"market": inputs0.market.model_copy(update={"price": price})})
    res = ReverseDCFModel().run(inputs, Assumptions(**BASE))
    assert res.implied_growth is not None
    assert abs(res.implied_growth - g_true) < 1e-4
    assert abs(res.value_per_share - price) < 1e-3
    assert res.diagnostics["implied_terminal_roic"] is not None


def test_reverse_dcf_reports_when_no_root():
    inputs = synthetic_inputs(price=1e9)
    res = ReverseDCFModel().run(inputs, Assumptions(**BASE))
    assert res.implied_growth is None and res.warnings
