from app.valuation.assumptions import Assumptions
from app.valuation.models.fcff import FCFFModel
from tests.valuation.conftest import synthetic_inputs

HAND = dict(
    revenue_growth_path=[0.10] * 5,
    target_ebit_margin=0.20,
    margin_fade_years=1,
    tax_rate=0.25,
    tax_rate_marginal=0.25,
    capex_pct_revenue=0.07,
    da_pct_revenue=0.05,
    nwc_pct_revenue=0.10,
    sbc_pct_revenue=0.0,
    mid_year=False,
    wacc={"wacc": 0.09},
    terminal={"method": "gordon", "g": 0.02},
    bridge={"debt": 200.0, "cash": 100.0, "leases": 0.0, "minority": 0.0, "shares": 100.0},
)


def hand_value() -> float:
    rev, pv, fcff = 1000.0, 0.0, 0.0
    for i in range(1, 6):
        new = rev * 1.10
        ebit = new * 0.20
        fcff = ebit * 0.75 + new * 0.05 - new * 0.07 - (new - rev) * 0.10
        pv += fcff / 1.09**i
        rev = new
    tv = fcff * 1.02 / (0.09 - 0.02)
    ev = pv + tv / 1.09**5
    return (ev - 200 + 100) / 100


def test_fcff_matches_hand_computed_case():
    inputs = synthetic_inputs(revenue0=1000.0, margin=0.20)
    res = FCFFModel().run(inputs, Assumptions(**HAND))
    expected = hand_value()
    assert res.value_per_share is not None
    assert abs(res.value_per_share / expected - 1) < 0.005, (res.value_per_share, expected)
    assert abs(expected - 23.655) < 0.01  # sanity on the hand number itself
    proj = res.components["projection"]
    assert len(proj["years"]) == 5 and abs(proj["years"][0]["revenue"] - 1100.0) < 1e-9
    assert res.components["bridge"]["equity_value"] == res.equity_value
    assert 0.5 < proj["pv_terminal_share"] < 0.9


def test_mid_year_raises_value_and_exit_multiple_works():
    inputs = synthetic_inputs()
    base = FCFFModel().run(inputs, Assumptions(**HAND))
    mid = FCFFModel().run(inputs, Assumptions(**{**HAND, "mid_year": True}))
    assert mid.value_per_share > base.value_per_share
    em = FCFFModel().run(
        inputs, Assumptions(**{**HAND, "terminal": {"method": "exit_multiple", "multiple": 10.0}})
    )
    proj = em.components["projection"]
    last = proj["years"][-1]
    assert abs(proj["terminal_value"] - 10.0 * (last["ebit"] + last["da"])) < 1e-6


def test_defaults_resolve_from_history_and_terminal_g_capped_at_rf():
    inputs = synthetic_inputs()
    r = Assumptions(terminal={"g": 0.09}).resolve(inputs)
    assert r.terminal_g <= r.rf and any("capped" in w for w in r.warnings)
    assert abs(r.growth_path[0] - 0.08) < 1e-6  # historical CAGR
    assert r.growth_path[-1] < r.growth_path[0]  # fades toward terminal g
    assert abs(r.margin_path[-1] - 0.20) < 1e-6
    assert r.debt == 200.0 and r.cash == 100.0 and r.shares == 100.0
    assert 0.05 < r.wacc < 0.12
    res = FCFFModel().run(inputs, Assumptions())
    assert res.value_per_share and res.value_per_share > 0
    assert "growth_path" in res.diagnostics["derivation"]


def test_sbc_policy_reduces_value():
    inputs = synthetic_inputs()
    a = Assumptions(**{**HAND, "sbc_pct_revenue": 0.03})
    with_sbc = FCFFModel().run(inputs, a.merged({"sbc_as_cash_expense": True}))
    without = FCFFModel().run(inputs, a.merged({"sbc_as_cash_expense": False}))
    assert with_sbc.value_per_share < without.value_per_share
