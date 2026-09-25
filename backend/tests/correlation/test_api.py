import json

import numpy as np
import pytest

from app.data.series_service import get_store
from app.market.service import get_ohlcv_store
from tests.correlation.conftest import ohlcv_from_close, synthetic_market


@pytest.fixture
def seeded(corr_client):
    m = synthetic_market(np.random.default_rng(7))
    st = get_store()
    for sid in ("fred:SP500", "fred:DGS10", "fred:VIXCLS"):
        st.write(sid, m[sid])
    get_ohlcv_store().write("SPY", ohlcv_from_close(m["SPY"]))
    return corr_client


def test_pairs_seeded_and_grouped(corr_client):
    r = corr_client.get("/api/correlation/pairs")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 50
    by_id = {p["id"]: p for p in body["items"]}
    assert by_id["il_usdils_qqq"]["lag_b"] == 1 and "hedge" in by_id["il_usdils_qqq"]["rationale"]
    assert {p["country"] for p in body["items"]} >= {"US", "IL"}
    il = corr_client.get("/api/correlation/pairs", params={"country": "IL"}).json()
    assert il["count"] >= 10 and all(p["country"] == "IL" for p in il["items"])


def test_pair_endpoint_full_methods(seeded):
    r = seeded.get(
        "/api/correlation/pair",
        params={
            "a": "SPY",
            "b": "fred:DGS10",
            "methods": "rolling,leadlag,beta,stationarity,coint,granger",
            "window": 40,
        },
    )
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["a"]["transform"] == "log_ret" and res["b"]["transform"] == "diff_bp" and res["freq"] == "1d"
    assert res["window"] == 40 and res["n"] > 400
    assert (
        res["stats"]["pearson"] < -0.4
        and res["stats"]["ci"][0] < res["stats"]["pearson"] < res["stats"]["ci"][1]
    )
    for key in (
        "frame",
        "rolling",
        "lead_lag",
        "ols",
        "rolling_beta",
        "stationarity",
        "granger_newbold",
        "cointegration",
        "granger",
        "warnings",
    ):
        assert key in res
    assert res["stationarity"]["a"]["verdict"] == "stationary"
    # overrides + invert + lag
    r2 = seeded.get(
        "/api/correlation/pair",
        params={
            "a": "SPY",
            "b": "fred:DGS10",
            "transform_b": "diff",
            "invert_b": "true",
            "lag_b": 1,
            "methods": "leadlag",
        },
    )
    assert r2.status_code == 200
    j = r2.json()
    assert (
        j["b"]["transform"] == "diff"
        and j["b"]["inverted"] is True
        and j["lag_b"] == 1
        and j["stats"]["pearson"] > 0
    )


def test_pair_endpoint_errors(seeded):
    assert seeded.get("/api/correlation/pair", params={"a": "SPY", "b": "nope:ZZZ"}).status_code == 404
    assert (
        seeded.get("/api/correlation/pair", params={"a": "SPY", "b": "fred:DGS2"}).status_code == 422
    )  # no data stored
    assert (
        seeded.get(
            "/api/correlation/pair", params={"a": "SPY", "b": "fred:DGS10", "transform_a": "bogus"}
        ).status_code
        == 400
    )
    assert (
        seeded.get(
            "/api/correlation/pair", params={"a": "SPY", "b": "fred:DGS10", "regime": "{bad"}
        ).status_code
        == 400
    )


def test_pair_regimes_preset_and_windows(seeded):
    r = seeded.get(
        "/api/correlation/pair", params={"a": "SPY", "b": "fred:DGS10", "methods": "rolling", "regime": "vix"}
    )
    assert r.status_code == 200 and r.json()["regimes"]["rows"]
    base = r.json()
    mid = base["frame"]["ts"][len(base["frame"]["ts"]) // 2]
    win = json.dumps(
        {"kind": "windows", "params": {"windows": [{"start": base["start"], "end": mid, "label": "H2"}]}}
    )
    r2 = seeded.get(
        "/api/correlation/pair", params={"a": "SPY", "b": "fred:DGS10", "methods": "rolling", "regime": win}
    )
    assert r2.status_code == 200 and r2.json()["regimes"]["rows"][0]["regime"] == "H2"
    presets = seeded.get("/api/correlation/regimes/presets").json()
    assert any(p["id"] == "vix" for p in presets)


def test_matrix_and_discover_endpoints(seeded):
    r = seeded.get(
        "/api/correlation/matrix", params={"ids": "SPY,fred:SP500,fred:DGS10,fred:VIXCLS", "window": 2000}
    )
    assert r.status_code == 200, r.text
    m = r.json()
    assert (
        len(m["labels"]) == 4
        and len(m["matrix"]) == 4
        and len(m["clusters"]) == 4
        and m["pc1_share"] is not None
    )
    assert seeded.get("/api/correlation/matrix", params={"ids": "SPY"}).status_code == 422
    d = seeded.get(
        "/api/correlation/discover",
        params={"x": "SPY", "universe": "fred:SP500,fred:DGS10,fred:VIXCLS", "window": 2000},
    ).json()
    assert d["rows"][0]["y"] == "fred:SP500" and d["rows"][0]["pearson"] > 0.95 and d["cached"] is False
    # catalog universe = enabled daily catalog series with stored data
    d2 = seeded.get(
        "/api/correlation/discover", params={"x": "SPY", "universe": "catalog", "window": 2000}
    ).json()
    assert {r["y"] for r in d2["rows"]} == {"fred:SP500", "fred:DGS10", "fred:VIXCLS"}
    assert seeded.get("/api/correlation/discover", params={"x": "nope:X"}).status_code == 404


def test_precompute_then_cached_reads(seeded):
    out = seeded.post("/api/correlation/precompute", params={"universe": "catalog"}).json()
    assert out[0]["status"] == "ok" and out[0]["n_series"] == 3
    m = seeded.get("/api/correlation/matrix", params={"universe": "catalog"}).json()
    assert m["cached"] is True and set(m["labels"]) == {"fred:SP500", "fred:DGS10", "fred:VIXCLS"}
    d = seeded.get("/api/correlation/discover", params={"x": "fred:SP500", "universe": "catalog"}).json()
    assert d["cached"] is True and {r["y"] for r in d["rows"]} == {"fred:DGS10", "fred:VIXCLS"}
    live = seeded.get("/api/correlation/matrix", params={"universe": "catalog", "live": "true"}).json()
    assert live["cached"] is False


def test_custom_pair_crud(seeded):
    r = seeded.post(
        "/api/correlation/pairs",
        json={
            "name": "SPY vs 10y",
            "a": "SPY",
            "b": "fred:DGS10",
            "freq": "1w",
            "lag_b": 1,
            "tags": ["mine"],
            "country": "US",
        },
    )
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["is_seed"] is False and p["freq"] == "1w"
    ids = {x["id"] for x in seeded.get("/api/correlation/pairs").json()["items"]}
    assert p["id"] in ids
    assert seeded.post("/api/correlation/pairs", json={"a": "SPY", "b": "nope:Q"}).status_code == 400
    assert seeded.delete(f"/api/correlation/pairs/{p['id']}").status_code == 200
    assert seeded.delete(f"/api/correlation/pairs/{p['id']}").status_code == 404
