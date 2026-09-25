import numpy as np

from app.analytics import pca
from tests.correlation.conftest import days, obs


def _block_universe(rng, n=500):
    ts = days(n)
    f = rng.standard_normal(n)
    return {
        "s1": obs(ts, f + 0.3 * rng.standard_normal(n)),
        "s3": obs(ts, rng.standard_normal(n)),  # deliberately out of order: independent series
        "s2": obs(ts, f + 0.3 * rng.standard_normal(n)),
    }


def test_matrix_ordering_clusters_and_pc1_share_on_block_structure(rng):
    res = pca.analyze_universe(_block_universe(rng), min_overlap=60)
    labels = res["labels"]
    assert set(labels) == {"s1", "s2", "s3"} and res["n_series"] == 3
    # clustering puts the correlated pair next to each other and apart from the independent series
    i1, i2, i3 = labels.index("s1"), labels.index("s2"), labels.index("s3")
    assert abs(i1 - i2) == 1
    cl = dict(zip(labels, res["clusters"], strict=True))
    assert cl["s1"] == cl["s2"] != cl["s3"]
    m = np.array(res["matrix"], dtype=float)
    assert np.allclose(np.diag(m), 1.0) and np.allclose(m, m.T)
    assert m[i1, i2] > 0.85 and abs(m[i1, i3]) < 0.15
    # eigenvalues ≈ (1+ρ, 1, 1−ρ) → share ≈ (1+ρ)/3 ≈ 0.63
    assert 0.55 < res["pc1_share"] < 0.72
    assert res["counts"][0][0] == 500 and res["dropped"] == []


def test_pairwise_complete_with_short_series_and_min_overlap(rng):
    frames = _block_universe(rng, 300)
    frames["short"] = obs(days(30), rng.standard_normal(30))
    res = pca.analyze_universe(frames, min_overlap=60)
    assert "short" in res["dropped"] and "short" not in res["labels"]
    # partially overlapping series: the pair with too little overlap is None, not a bogus number
    frames2 = _block_universe(rng, 300)
    frames2["late"] = obs(days(400)[250:], rng.standard_normal(150))
    res2 = pca.analyze_universe(frames2, min_overlap=60)
    assert "late" in res2["labels"]
    li = res2["labels"].index("late")
    row = res2["matrix"][li]
    assert row[li] == 1.0 and any(v is None for v in row)  # <60 common rows with the others → None


def test_ledoit_wolf_shrinkage_note_and_fallback(rng):
    frames = _block_universe(rng, 400)
    res = pca.analyze_universe(frames, min_overlap=60, shrink=True)
    assert any("Ledoit" in n for n in res["notes"])
    assert 0.5 < res["pc1_share"] < 0.75
    # a late-starting series leaves only 50 complete-case rows (< min_overlap) → shrinkage falls back to pairwise
    frames["late"] = obs(days(500)[350:], rng.standard_normal(150))
    res2 = pca.analyze_universe(frames, min_overlap=60, shrink=True)
    assert "late" in res2["labels"] and any("skipped" in n for n in res2["notes"])


def test_empty_universe():
    res = pca.analyze_universe({}, min_overlap=10)
    assert res["labels"] == [] and res["pc1_share"] is None
