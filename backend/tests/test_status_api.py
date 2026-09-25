def test_catalog_meta_has_value_and_freshness(client):
    csv = "date,value\n2026-07,49.1\n2026-08,48.7\n"
    assert client.post("/api/catalog/manual:ISM_MFG_PMI/manual", json={"csv": csv}).status_code == 200
    items = {i["series_id"]: i for i in client.get("/api/catalog").json()["items"]}
    m = items["manual:ISM_MFG_PMI"]["meta"]
    assert m["last_value"] == 48.7 and m["freshness"] in ("ok", "late") and m["expected_by"]
    fr = {r["series_id"]: r for r in client.get("/api/sys/freshness").json()}
    assert fr["manual:ISM_MFG_PMI"]["freshness"] == m["freshness"]


def test_observations_carry_spec_and_derived_attribution(client):
    obs = client.get("/api/series/derived:US2S10S_BP/observations").json()
    assert obs["spec"]["formula"] and obs["spec"]["provider"] == "derived"
    assert obs["attribution"] and "FRED" in obs["attribution"]  # inherited from fred:DGS10 / fred:DGS2
    assert client.get("/api/series/fred:DGS10/observations").json()["spec"]["value_kind"] == "yield"


def test_jobs_include_last_run(client):
    j = client.get("/api/sys/jobs").json()
    assert all("last_run" in s and "last_ok_at" in s and "last_work_at" in s for s in j["scheduled"])


def test_backups_have_created_at(client):
    client.post("/api/sys/backups/run")
    b = client.get("/api/sys/backups").json()
    assert b and b[0]["created_at"] and "files" in b[0]


async def test_jobs_last_run_last_ok_and_last_work(db, monkeypatch):
    from datetime import datetime

    from app.api.routers import sys as sysr
    from app.data.store.models import JobRun
    from app.data.store.sqlite import session_scope

    monkeypatch.setattr(sysr, "jobs_snapshot", lambda: [{"id": "market.quotes_poll", "name": "Quotes poll"}])
    async with session_scope() as s:
        s.add(
            JobRun(
                job_id="market.quotes_poll",
                status="ok",
                message="27/27 quotes",
                finished_at=datetime(2026, 9, 24, 10),
            )
        )
        s.add(
            JobRun(
                job_id="market.quotes_poll", status="ok", message="", finished_at=datetime(2026, 9, 24, 11)
            )
        )
        s.add(
            JobRun(
                job_id="market.quotes_poll",
                status="error",
                message="boom",
                finished_at=datetime(2026, 9, 24, 12),
            )
        )
    job = (await sysr.jobs())["scheduled"][0]
    assert job["last_run"]["status"] == "error" and job["last_run"]["message"] == "boom"
    assert job["last_ok_at"] == datetime(2026, 9, 24, 11)
    assert job["last_work_at"] == datetime(2026, 9, 24, 10)
