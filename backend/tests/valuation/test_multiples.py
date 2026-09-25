from app.valuation.assumptions import Assumptions
from app.valuation.models.multiples import MultiplesModel, company_metrics, current_multiples, implied_price
from tests.valuation.conftest import synthetic_inputs


def test_implied_prices_per_metric():
    inputs = synthetic_inputs(price=20.0)
    m = company_metrics(inputs)
    # latest FY: revenue 1000, ebit 200, da 50 -> ebitda 250; net debt 200 - 100 = 100; shares 100
    assert abs(m["ebitda"] - 250.0) < 1e-6 and abs(m["net_debt"] - 100.0) < 1e-6
    assert abs(implied_price("ev_ebitda", 10.0, m) - (2500 - 100) / 100) < 1e-9
    assert abs(implied_price("ev_sales", 2.0, m) - (2000 - 100) / 100) < 1e-9
    eps = m["eps"]
    assert abs(implied_price("pe", 15.0, m) - 15 * eps) < 1e-9
    assert abs(implied_price("ps", 1.5, m) - 15.0) < 1e-9
    assert abs(implied_price("pb", 2.0, m) - 16.0) < 1e-9
    cur = current_multiples(m)
    assert abs(cur["ev_ebitda"] - (2000 + 100) / 250) < 1e-9


def test_multiples_model_median_and_range():
    inputs = synthetic_inputs(price=20.0)
    peers = {
        "ev_ebitda": {"p25": 8.0, "median": 10.0, "p75": 12.0},
        "pe": {"p25": 12.0, "median": 15.0, "p75": 18.0},
    }
    res = MultiplesModel().run(inputs, Assumptions(peer_stats=peers, multiples_use="peers"))
    m = company_metrics(inputs)
    ev_base = implied_price("ev_ebitda", 10.0, m)
    pe_base = implied_price("pe", 15.0, m)
    assert res.value_per_share == (sorted([ev_base, pe_base])[0] + sorted([ev_base, pe_base])[1]) / 2
    assert res.low < res.value_per_share < res.high
    assert set(res.components["metrics"]) == {"ev_ebitda", "pe"}
    assert res.components["metrics"]["ev_ebitda"]["current"] is not None
    empty = MultiplesModel().run(inputs, Assumptions())
    assert empty.value_per_share is None and empty.warnings
