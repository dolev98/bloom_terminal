from datetime import UTC, datetime

from app.data.providers.base import Quote
from app.market import service as market_service
from tests.valuation.conftest import synthetic_inputs


def _seed_quote(ticker="AAPL", last=20.0, source="finnhub"):
    market_service._quotes[ticker] = Quote(ticker=ticker, ts=datetime.now(tz=UTC), last=last, source=source)


def test_models_endpoint_lists_builtins_and_user_models(vclient):
    r = vclient.get("/api/valuation/models").json()
    ids = {m["id"] for m in r["models"]}
    assert {"fcff", "reverse_dcf", "multiples", "external", "graham_number"} <= ids
    assert vclient.post("/api/valuation/models/reload").json()["loaded"] == ["graham_number"]
    pol = vclient.get("/api/valuation/policies").json()
    assert pol["sbc_cash_expense"] is True
    assert vclient.put("/api/valuation/policies", json={"mid_year": True}).json()["mid_year"] is True


def test_run_valuation_stores_fair_values_and_series(vclient, monkeypatch):
    from app.valuation import service

    _seed_quote()

    async def fake_inputs(ticker, benchmark="^GSPC"):
        return synthetic_inputs(price=20.0, ticker=ticker, with_consensus=True)

    monkeypatch.setattr(service, "build_inputs", fake_inputs)
    body = {
        "model_ids": ["fcff", "reverse_dcf", "multiples", "graham_number"],
        "assumptions": {
            "wacc": {"wacc": 0.09},
            "terminal": {"g": 0.02},
            "peer_stats": {"pe": {"p25": 12, "median": 15, "p75": 18}},
        },
    }
    r = vclient.post("/api/valuation/AAPL/run", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (
        out["results"]["fcff"]["value_per_share"] > 0
        and out["results"]["reverse_dcf"]["implied_growth"] is not None
    )
    assert out["assumption_set"]["source"] == "manual" and out["blended"]["value"] is not None
    assert {row["method"] for row in out["football_field"]} >= {
        "DCF (bear/base/bull)",
        "Peer multiples",
        "Analyst targets",
        "Blended",
        "52-week range",
    }
    assert out["sensitivity"]["wacc_g"]["values"]
    g = vclient.get("/api/valuation/AAPL").json()
    models = {(f["model"], f["scenario"]) for f in g["fair_values"]}
    assert {
        ("fcff", "base"),
        ("fcff", "bear"),
        ("fcff", "bull"),
        ("reverse_dcf", "base"),
        ("multiples", "base"),
        ("graham_number", "base"),
        ("blended", "base"),
        ("analyst", "base"),
    } <= models
    assert (
        g["reference"]["name"] == "base_dcf"
        and g["reference"]["upside"] is not None
        and g["sensitivity"] is not None
    )
    assert any(r["method"].startswith("User model") for r in g["football_field"])
    obs = vclient.get("/api/series/val:AAPL:fair_value_base/observations").json()
    assert obs["n"] == 1 and abs(obs["value"][0] - out["results"]["fcff"]["value_per_share"]) < 1e-9
    sets = vclient.get("/api/valuation/AAPL/assumptions").json()
    assert sets["sets"] and "properties" in sets["schema"]
    derived = vclient.post(
        "/api/valuation/AAPL/assumptions",
        json={
            "parent_id": sets["sets"][0]["id"],
            "assumptions": {"wacc": {"wacc": 0.10}},
            "name": "higher wacc",
        },
    ).json()
    d = vclient.get(f"/api/valuation/AAPL/assumptions/{derived['id']}/diff").json()
    assert d["diff"] == {"wacc.wacc": {"from": 0.09, "to": 0.10}}
    r2 = vclient.post(
        "/api/valuation/AAPL/run",
        json={"model_ids": ["fcff"], "assumption_set_id": derived["id"], "with_sensitivity": False},
    ).json()
    assert r2["results"]["fcff"]["value_per_share"] < out["results"]["fcff"]["value_per_share"]


def test_import_csv_and_ginzu_then_run_external(vclient, monkeypatch):
    from app.valuation import service

    _seed_quote()

    async def fake_inputs(ticker, benchmark="^GSPC"):
        return synthetic_inputs(price=20.0, ticker=ticker)

    monkeypatch.setattr(service, "build_inputs", fake_inputs)
    csv = "key,value,unit,scenario,note\nfair_value,31,USD,base,sheet\n"
    r = vclient.post(
        "/api/valuation/AAPL/import", data={"kind": "csv"}, files={"file": ("fv.csv", csv, "text/csv")}
    )
    assert r.status_code == 200, r.text
    sid = r.json()["sets"][0]["id"]
    assert r.json()["sets"][0]["source"] == "import_csv"
    run = vclient.post(
        "/api/valuation/AAPL/run",
        json={"model_ids": ["external"], "assumption_set_id": sid, "with_sensitivity": False},
    ).json()
    assert (
        run["results"]["external"]["value_per_share"] == 31
        and abs(run["results"]["external"]["upside"] - 0.55) < 1e-9
    )
    assert vclient.post("/api/valuation/AAPL/import", data={"kind": "nope"}).status_code == 400
    peers = vclient.put(
        "/api/valuation/AAPL/peers", json={"pins": ["msft", "GOOG"], "excludes": ["goog"]}
    ).json()
    assert peers["effective"] == ["MSFT"]
    assert vclient.get("/api/valuation/AAPL/peers").json()["pins"] == ["MSFT", "GOOG"]
    macro = vclient.get("/api/valuation/macro").json()
    assert "macro" in macro and "industry_stats" in macro
