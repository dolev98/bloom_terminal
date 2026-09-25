import numpy as np
import polars as pl

from app.analytics import correlation as C
from tests.correlation.conftest import ar1, days, random_walk


def _aligned(x, y) -> pl.DataFrame:
    n = len(x)
    return pl.DataFrame({"ts": days(n), "a": np.asarray(x, dtype=float), "b": np.asarray(y, dtype=float)})


def test_pearson_on_known_linear_relationship(rng):
    n = 1000
    x = rng.standard_normal(n)
    y = 2.0 * x + 1.0 * rng.standard_normal(n)  # theoretical ρ = 2/√5 ≈ 0.894
    st = C.pearson_spearman(x, y)
    assert abs(st["pearson"] - 0.894) < 0.03
    assert st["ci"][0] < st["pearson"] < st["ci"][1]
    assert st["pearson_p"] < 1e-6 and st["spearman_p"] < 1e-6
    assert abs(st["spearman"] - st["pearson"]) < 0.05
    assert st["n"] == n and abs(st["n_eff"] - n) < 0.1 * n  # white noise → no correction
    assert abs(st["r2"] - st["pearson"] ** 2) < 1e-3  # both rounded to 4 dp


def test_effective_n_shrinks_for_autocorrelated_inputs(rng):
    n = 800
    x = ar1(rng, n, 0.9)
    y = 0.5 * x + ar1(rng, n, 0.9)
    n_eff = C.effective_n(x, y)
    assert n_eff < 0.3 * n
    st = C.pearson_spearman(x, y)
    assert st["ci_eff"][1] - st["ci_eff"][0] > st["ci"][1] - st["ci"][0]


def test_lead_lag_recovers_planted_lag(rng):
    n = 600
    x = rng.standard_normal(n)
    b = np.roll(x, 3)  # B_t = A_{t-3} → A leads B by 3 → k = -3
    b[:3] = rng.standard_normal(3)
    ll = C.lead_lag(x, b, max_lag=6)
    assert ll["best_lag"] == -3 and ll["best_corr"] > 0.95
    assert len(ll["lags"]) == 13 and ll["band"] == round(2 / np.sqrt(n), 4)
    rev = C.lead_lag(b, x, max_lag=6)  # now B leads A → k = +3
    assert rev["best_lag"] == 3
    zero = C.lead_lag(x, 0.7 * x + rng.standard_normal(n), max_lag=4)
    assert zero["best_lag"] == 0


def test_rolling_corr_pearson_spearman_and_ewma(rng):
    n = 400
    x = rng.standard_normal(n)
    y = 0.8 * x + 0.6 * rng.standard_normal(n)
    df = _aligned(x, y)
    rp = C.rolling_corr(df, 60)
    assert rp.columns == ["ts", "corr", "lo", "hi", "n"] and rp.height == n
    assert rp["corr"][:47].is_null().all()  # min_periods = 0.8·60 = 48
    tail = rp.tail(200)
    assert tail["corr"].is_between(-1, 1).all()
    assert (tail["lo"] <= tail["corr"]).all() and (tail["corr"] <= tail["hi"]).all()
    assert abs(tail["corr"].mean() - 0.8) < 0.15
    rs = C.rolling_corr(df, 60, method="spearman")
    assert rs.height == n and abs(rs.tail(200)["corr"].mean() - 0.78) < 0.15
    ew = C.ewma_corr(df, halflife=20)
    assert ew.height == n and abs(ew.tail(200)["corr"].mean() - 0.8) < 0.15


def test_ols_and_rolling_beta_recover_slope(rng):
    n = 500
    x = rng.standard_normal(n)
    y = 0.5 + 2.0 * x + 0.5 * rng.standard_normal(n)
    res = C.ols(y, x)
    assert abs(res["beta"] - 2.0) < 0.1 and abs(res["alpha"] - 0.5) < 0.1
    assert res["t_beta"] > 10 and res["r2"] > 0.9 and res["hac_lags"] >= 1
    rb = C.rolling_beta(_aligned(y, x), 100)
    assert rb.columns == ["ts", "beta", "alpha", "r2"]
    assert abs(rb.tail(300)["beta"].mean() - 2.0) < 0.15


def test_cointegration_detects_planted_pair_and_rejects_independent_walks(rng):
    n = 800
    x = random_walk(rng, n, 100.0, 1.0)
    y = 10.0 + 2.0 * x + rng.standard_normal(n)  # spread is white noise → cointegrated
    res = C.cointegration(y, x, z_window=60)
    assert res["eg_p"] < 0.05
    assert abs(res["hedge_ratio"] - 2.0) < 0.05 and abs(res["intercept"] - 10.0) < 2.0
    assert res["johansen"]["rank"] >= 1
    assert res["half_life"] is not None and 0 < res["half_life"] < 5
    assert res["spread_adf_p"] < 0.05
    assert res["verdict"] == "cointegrated"
    assert len(res["spread"]) == n and len(res["zscore"]) == n and res["z_last"] is not None
    indep = C.cointegration(random_walk(rng, n), random_walk(rng, n))
    assert indep["eg_p"] > 0.05 and indep["verdict"] == "none"


def test_half_life_of_ar1_spread(rng):
    s = ar1(rng, 2000, 0.9)  # theoretical half-life = ln(0.5)/ln(0.9) ≈ 6.6
    hl = C.half_life(s)
    assert 4 < hl < 10


def test_granger_direction(rng):
    n = 600
    a = rng.standard_normal(n)
    b = np.zeros(n)
    for i in range(1, n):
        b[i] = 0.8 * a[i - 1] + 0.3 * rng.standard_normal()
    res = C.granger(a, b, maxlag=3)
    assert res["a_to_b"]["p"] < 0.01 and 1 <= res["a_to_b"]["lag"] <= 3  # lag is AIC-selected
    assert res["b_to_a"]["p"] > 0.05
