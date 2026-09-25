import numpy as np
import polars as pl

from app.analytics import regimes as R
from tests.correlation.conftest import days, obs


def test_bands_and_trend_and_windows(rng):
    ts = days(300)
    vix = obs(ts, np.linspace(10, 40, 300))
    b = R.bands_regime(vix, [15, 25], ["calm", "normal", "stress"])
    assert b["regime"][0] == "calm" and b["regime"][-1] == "stress"
    assert set(b["regime"].unique().to_list()) == {"calm", "normal", "stress"}
    dff = obs(ts, np.concatenate([np.zeros(100), np.linspace(0, 2, 100), np.full(100, 2.0)]))
    t = R.trend_regime(dff, periods=20, threshold=0.1)
    assert t.filter(pl.col("ts") == ts[150])["regime"][0] == "hiking"
    assert t.filter(pl.col("ts") == ts[290])["regime"][0] == "hold"
    w = R.windows_regime(ts, [{"start": "2022-03-01", "end": "2022-03-31", "label": "march"}], other="rest")
    counts = dict(w["regime"].value_counts().iter_rows())
    assert counts["march"] == 31 and counts["rest"] == 300 - 31


def test_vol_quantiles_split_roughly_evenly(rng):
    r = obs(days(600), rng.standard_normal(600))
    v = R.vol_quantile_regime(r, window=20)
    counts = dict(v["regime"].value_counts().iter_rows())
    assert set(counts) == {"low_vol", "mid_vol", "high_vol"}
    assert all(150 < c < 250 for c in counts.values())


def test_conditional_corr_recovers_sign_per_regime(rng):
    n = 600
    ts = days(n)
    x = rng.standard_normal(n)
    noise = 0.4 * rng.standard_normal(n)
    y = np.where(np.arange(n) < 300, x, -x) + noise
    aligned = pl.DataFrame({"ts": ts, "a": x, "b": y})
    regime = pl.DataFrame({"ts": ts[[0, 300]], "regime": ["up", "down"]})  # as-of labels
    rows = {r["regime"]: r for r in R.conditional_corr(aligned, regime)}
    assert rows["up"]["n"] == 300 and rows["down"]["n"] == 300
    assert rows["up"]["pearson"] > 0.85 and rows["down"]["pearson"] < -0.85
    assert rows["up"]["ci"][0] < rows["up"]["pearson"] < rows["up"]["ci"][1]


def test_markov_regime_separates_volatility_states(rng):
    y = np.concatenate([0.5 * rng.standard_normal(300), 3.0 * rng.standard_normal(300)])
    df = obs(days(600), y)
    m = R.markov_regime(df, k=2)
    assert m is not None and set(m["regime"].unique().to_list()) <= {"low_vol", "high_vol"}
    first, second = m["regime"][:300], m["regime"][300:]
    assert (first == "low_vol").mean() > 0.8 and (second == "high_vol").mean() > 0.8
    assert R.markov_regime(df.head(50)) is None  # too short → None, never raises


def test_presets_are_well_formed():
    ids = [p["id"] for p in R.PRESETS]
    assert len(ids) == len(set(ids)) and {"vix", "curve", "fed", "realized_vol", "markov"} <= set(ids)
    for p in R.PRESETS:
        assert p["kind"] in ("bands", "trend", "vol_quantiles", "markov")
        if p["kind"] == "bands":
            assert len(p["params"]["labels"]) == len(p["params"]["edges"]) + 1
