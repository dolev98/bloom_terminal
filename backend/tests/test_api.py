def test_health_and_catalog_seeded(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"]
    cat = client.get("/api/catalog").json()
    assert cat["count"] > 40
    ids = {i["series_id"] for i in cat["items"]}
    assert {"fred:DGS10", "boi:EXR/RER_USD_ILS", "derived:NET_LIQ_BN"} <= ids


def test_manual_upload_and_observations(client):
    csv = "date,value\n2026-07,49.1\n2026-08,48.7\n"
    r = client.post("/api/catalog/manual:ISM_MFG_PMI/manual", json={"csv": csv})
    assert r.status_code == 200 and r.json()["rows_added"] == 2
    obs = client.get("/api/series/manual:ISM_MFG_PMI/observations").json()
    assert obs["n"] == 2 and obs["value"] == [49.1, 48.7]
    diff = client.get("/api/series/manual:ISM_MFG_PMI/observations", params={"transform": "diff"}).json()
    assert diff["n"] == 1 and abs(diff["value"][0] + 0.4) < 1e-9


def test_resolve_id_and_providers(client):
    r = client.post("/api/catalog/resolve", json={"text": "manual:AAII_BULL"})
    assert r.status_code == 200 and r.json()["series_id"] == "manual:AAII_BULL"
    provs = client.get("/api/catalog/providers").json()
    assert {p["id"] for p in provs} >= {"fred", "boi", "edgar", "hebcal", "finnhub", "manual"}


def test_sys_endpoints(client):
    assert client.get("/api/sys/jobs").status_code == 200
    assert client.get("/api/sys/quotas").status_code == 200
    fr = client.get("/api/sys/freshness").json()
    assert any(row["series_id"] == "fred:DGS10" and row["stale"] for row in fr)
    s = client.get("/api/sys/settings").json()
    assert s["secrets"]["fred_api_key"]["set"] is True
    assert (
        client.put("/api/sys/prefs", json={"key": "grey_sources_enabled", "value": False}).status_code == 200
    )
    assert client.get("/api/health").json()["grey_sources_enabled"] is False
