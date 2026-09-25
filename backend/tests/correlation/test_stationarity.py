import numpy as np

from app.analytics import stationarity as st
from tests.correlation.conftest import random_walk


def test_white_noise_is_stationary(rng):
    res = st.adf_kpss(rng.standard_normal(500))
    assert res["verdict"] == "stationary"
    assert res["adf_p"] < 0.05 and res["kpss_p"] >= 0.05


def test_random_walk_is_i1_like(rng):
    res = st.adf_kpss(random_walk(rng, 500))
    assert res["verdict"] == "I(1)-like"
    assert res["adf_p"] >= 0.05 and res["kpss_p"] < 0.05


def test_short_or_constant_series_is_insufficient():
    assert st.adf_kpss(np.ones(50))["verdict"] == "insufficient"
    assert st.adf_kpss(np.arange(5.0))["verdict"] == "insufficient"


def test_granger_newbold_flags_levels_of_independent_random_walks(rng):
    a = random_walk(rng, 600)
    b = random_walk(rng, 600)
    res = st.granger_newbold_warning(a, b)
    assert (
        res["dw"] is not None and res["dw"] < 0.5
    )  # residuals of an I(1)-on-I(1) regression are highly persistent
    assert res["spurious"] == (res["r2"] > res["dw"])
    ok = st.granger_newbold_warning(rng.standard_normal(600), rng.standard_normal(600))
    assert ok["spurious"] is False and ok["dw"] > 1.5
