import polars as pl
import pytest

from app.analytics.align import AlignmentError
from app.api.routers import watchlists
from app.correlation import service
from app.data.catalog.loader import load_seed
from app.data.series_service import get_store
from app.data.store.ohlcv import OhlcvStore
from app.jobs.tasks.correlation import precompute_correlation_job
from app.market.service import get_ohlcv_store, set_ohlcv_store
from tests.correlation.conftest import month_starts, obs, ohlcv_from_close, synthetic_market


@pytest.fixture
async def market(db, rng, tmp_path):
    await load_seed()
    set_ohlcv_store(OhlcvStore(tmp_path / "parquet"))
    m = synthetic_market(rng)
    st = get_store()
    for sid in ("fred:SP500", "fred:DGS10", "fred:VIXCLS"):
        st.write(sid, m[sid])
    get_ohlcv_store().write("SPY", ohlcv_from_close(m["SPY"]))
    qqq = m["SPY"].with_columns(
        (pl.col("value") * 1.5 + pl.Series(rng.standard_normal(m["SPY"].height) * 2)).alias("value")
    )
    get_ohlcv_store().write("QQQ", ohlcv_from_close(qqq))
    yield m
    set_ohlcv_store(None)


def test_ticker_from_id():
    assert service.ticker_from_id("spy") == "SPY"
    assert service.ticker_from_id("yf:AAPL:close") == "AAPL"
    assert service.ticker_from_id("TA35.TA") == "TA35.TA" and service.ticker_from_id("^SOX") == "^SOX"
    assert (
        service.ticker_from_id("fred:DGS10") is None and service.ticker_from_id("boi:EXR/RER_USD_ILS") is None
    )


async def test_analyze_pair_ticker_vs_catalog(market):
    res = await service.analyze_pair(
        "SPY", "fred:DGS10", methods=["rolling", "leadlag", "beta", "stationarity", "coint", "granger"]
    )
    assert res["a"]["kind"] == "ohlcv" and res["a"]["transform"] == "log_ret"
    assert res["b"]["kind"] == "catalog" and res["b"]["transform"] == "diff_bp"
    assert res["freq"] == "1d" and res["window"] == 60 and res["n"] > 500
    assert res["stats"]["pearson"] < -0.4 and res["stats"]["pearson_p"] < 1e-6
    assert res["stats"]["n_eff"] is not None
    f = res["frame"]
    assert len(f["ts"]) == len(f["a"]) == len(f["b"]) == len(f["a_t"]) == len(f["b_t"])
    assert f["a_t"][0] is None and f["a_t"][1] is not None  # first return is null
    r = res["rolling"]
    assert len(r["ts"]) == res["n"] and len(r["pearson"]) == len(r["lo"]) == len(r["spearman"]) == len(
        r["ewma"]
    )
    assert res["lead_lag"]["best_lag"] == 0 and len(res["lead_lag"]["lags"]) == 21
    assert res["ols"]["beta"] < 0 and len(res["rolling_beta"]["beta"]) == res["n"]
    assert (
        res["stationarity"]["a"]["verdict"] == "stationary"
        and res["stationarity"]["a_level"]["verdict"] == "I(1)-like"
    )
    assert res["cointegration"]["verdict"] in ("none", "weak", "cointegrated") and len(
        res["cointegration"]["spread"]
    ) == len(f["ts"])
    assert res["granger"]["a_to_b"] is not None
    assert "stability" in res and isinstance(res["warnings"], list)


async def test_analyze_pair_lag_matches_lead_lag_and_invert_flips_sign(market):
    base = await service.analyze_pair("SPY", "fred:DGS10", methods=["leadlag"])
    at1 = next(r["corr"] for r in base["lead_lag"]["lags"] if r["lag"] == 1)
    lagged = await service.analyze_pair("SPY", "fred:DGS10", lag_b=1, methods=["leadlag"])
    assert lagged["lag_b"] == 1 and abs(lagged["stats"]["pearson"] - at1) < 0.02
    inv = await service.analyze_pair("SPY", "fred:DGS10", invert_b=True, methods=["leadlag"])
    assert abs(inv["stats"]["pearson"] + base["stats"]["pearson"]) < 1e-6 and inv["b"]["inverted"] is True


async def test_analyze_pair_coarsens_to_monthly_with_warning(market):
    months = month_starts(40)
    get_store().write("fred:M2SL", obs(months, [20000 + 50 * i for i in range(months.len())]))
    res = await service.analyze_pair(
        "fred:SP500", "fred:M2SL", freq="1d", methods=["rolling"], min_overlap=12
    )
    assert res["freq"] == "1mo" and any("upsample" in w for w in res["warnings"])
    assert res["b"]["transform"] == "yoy" and res["window"] <= 24 and res["n"] >= 12
    with pytest.raises(AlignmentError):
        await service.analyze_pair("fred:SP500", "fred:M2SL", freq="1d", methods=["rolling"], min_overlap=60)


async def test_analyze_pair_regime_preset_and_windows(market):
    res = await service.analyze_pair("SPY", "fred:DGS10", methods=["rolling"], regime="vix")
    rows = res["regimes"]["rows"]
    assert rows and {r["regime"] for r in rows} <= {"calm", "normal", "stress"}
    mid = res["frame"]["ts"][len(res["frame"]["ts"]) // 2]
    win = {
        "kind": "windows",
        "id": "custom",
        "params": {"windows": [{"start": res["start"], "end": mid, "label": "H2-22"}]},
    }
    res2 = await service.analyze_pair("SPY", "fred:DGS10", methods=["rolling"], regime=win)
    assert [r["regime"] for r in res2["regimes"]["rows"]] == ["H2-22"]
    with pytest.raises(service.UnknownSeries):
        await service.analyze_pair("SPY", "nope:XYZ", methods=["rolling"])


async def test_discover_and_matrix(market):
    d = await service.discover("SPY", ["fred:SP500", "fred:DGS10", "fred:VIXCLS", "QQQ"], window_days=2000)
    rows = d["rows"]
    assert d["x_transform"] == "log_ret" and d["n_candidates"] == 4 and len(rows) == 4
    assert rows[0]["y"] == "fred:SP500" and rows[0]["pearson"] > 0.95
    assert [r["abs"] for r in rows] == sorted((r["abs"] for r in rows), reverse=True)
    assert all(
        k in rows[0] for k in ("spearman", "best_lag", "stability", "coint_p", "spark", "name", "transform")
    )
    assert (
        rows[0]["coint_p"] is not None and rows[0]["coint_p"] < 0.05
    )  # SPY ≈ SP500/10 → cointegrated levels
    m = await service.matrix(["SPY", "fred:SP500", "fred:DGS10", "fred:VIXCLS"], window_days=2000)
    assert set(m["labels"]) == {"SPY", "fred:SP500", "fred:DGS10", "fred:VIXCLS"}
    i, j = m["labels"].index("SPY"), m["labels"].index("fred:SP500")
    assert abs(i - j) == 1 and m["matrix"][i][j] > 0.95 and 0 < m["pc1_share"] <= 1
    assert m["transforms"]["fred:DGS10"] == "diff_bp" and m["cached"] is False


async def test_precompute_writes_and_reads_cache(market):
    await watchlists.ensure_default()
    assert set(await service.universe_ids("watchlist")) == {"SPY", "QQQ"}
    assert set(await service.universe_ids("catalog")) == {"fred:SP500", "fred:DGS10", "fred:VIXCLS"}
    out = await precompute_correlation_job(["watchlist", "catalog"])
    assert [o["status"] for o in out] == ["ok", "ok"]
    wl = service.read_matrix_cache("watchlist")
    assert wl["cached"] is True and set(wl["labels"]) == {"SPY", "QQQ"} and wl["matrix"][0][1] > 0.9
    disc = service.read_discover_cache("watchlist", "SPY")
    assert (
        disc["cached"] is True
        and disc["rows"][0]["y"] == "QQQ"
        and isinstance(disc["rows"][0]["spark"], list)
    )
    rev = service.read_discover_cache("watchlist", "QQQ")
    assert rev["rows"][0]["y"] == "SPY" and rev["rows"][0]["best_lag"] == -disc["rows"][0]["best_lag"]
    cat = service.read_discover_cache("catalog", "fred:SP500")
    assert cat and {r["y"] for r in cat["rows"]} == {"fred:DGS10", "fred:VIXCLS"}
    assert service.read_matrix_cache("nope") is None and service.read_discover_cache("catalog", "SPY") is None
    # idempotent re-run
    out2 = await precompute_correlation_job(["watchlist"])
    assert out2[0]["status"] == "ok"


async def test_pairs_seed_and_crud(db):
    n = await service.ensure_seed_pairs()
    assert n >= 50 and await service.ensure_seed_pairs() == 0
    items = {p["id"]: p for p in await service.list_pairs()}
    il = items["il_usdils_qqq"]
    assert (
        il["a"] == "boi:EXR/RER_USD_ILS"
        and il["b"] == "QQQ"
        and il["lag_b"] == 1
        and il["country"] == "IL"
        and il["rationale"]
    )
    assert all(p["is_seed"] for p in items.values()) and len(await service.list_pairs("IL")) >= 10
    saved = await service.save_pair(
        {"a": "SPY", "b": "fred:DGS10", "name": "mine", "freq": "1w", "lag_b": 2, "tags": ["x"]}
    )
    assert saved["is_seed"] is False and saved["lag_b"] == 2 and saved["id"]
    assert any(p["id"] == saved["id"] for p in await service.list_pairs())
    assert await service.delete_pair(saved["id"]) is True and await service.delete_pair(saved["id"]) is False


async def test_retired_seed_pairs_are_deleted(db, tmp_path):
    from app.correlation.models import Pair
    from app.data.store.sqlite import session_scope

    async with session_scope() as s:
        s.add(Pair(id="old_seed", name="old", a="X", b="Y", rationale="", tags=[], is_seed=True))
        s.add(Pair(id="mine", name="mine", a="X", b="Y", rationale="", tags=[], is_seed=False))
    seed = tmp_path / "pairs.yaml"
    seed.write_text("pairs: []\nretired: [old_seed, mine]\n", encoding="utf-8")
    assert await service.ensure_seed_pairs(seed) == 0
    ids = {p["id"] for p in await service.list_pairs()}
    assert "old_seed" not in ids and "mine" in ids  # only seed rows are pruned
    assert "il_chkp_dual" not in {p["id"] for p in await service.list_pairs()}
