from datetime import UTC, date, datetime

from app.data.providers.base import Quote
from app.market import service as market_service


def test_rule_types_schema(aclient):
    r = aclient.get("/api/alerts/rule-types").json()
    assert (
        "upside_gt" in r["rule_types"]
        and r["rule_types"]["upside_gt"]["direction"] == "above"
        and "base_dcf" in r["references"]
    )


def test_rules_crud_state_history_ack_snooze(aclient, monkeypatch):
    from app.alerts import engine
    from app.data.store.sqlite import session_scope
    from app.valuation.orm import FairValueDaily

    sent = []

    async def fake_send(text, ops=False, parse_mode="HTML"):
        sent.append(text)
        return True

    monkeypatch.setattr(engine, "send_telegram", fake_send)
    bad = aclient.post("/api/alerts/rules", json={"rule_type": "nope"})
    assert bad.status_code == 422
    rule = aclient.post(
        "/api/alerts/rules",
        json={
            "name": "AAPL upside",
            "ticker": "aapl",
            "rule_type": "upside_gt",
            "reference": "base_dcf",
            "threshold": 25,
            "quiet_hours": {"start": "00:00", "end": "00:00"},
        },
    ).json()
    assert rule["ticker"] == "AAPL" and rule["hysteresis_pp"] == 5.0 and rule["enabled"] is True
    assert aclient.get("/api/alerts/rules").json()[0]["id"] == rule["id"]
    upd = aclient.put(f"/api/alerts/rules/{rule['id']}", json={**rule, "threshold": 20}).json()
    assert upd["threshold"] == 20
    assert (
        aclient.patch(f"/api/alerts/rules/{rule['id']}", json={"enabled": False}).json()["enabled"] is False
    )
    assert aclient.patch(f"/api/alerts/rules/{rule['id']}", json={"enabled": True}).json()["enabled"] is True

    async def seed():
        async with session_scope() as s:
            s.add(
                FairValueDaily(
                    ticker="AAPL",
                    date=date(2026, 9, 22),
                    model="fcff",
                    scenario="base",
                    value=130.0,
                    price=100.0,
                    price_source="finnhub",
                )
            )

    aclient.portal.call(seed)
    market_service._quotes["AAPL"] = Quote(
        ticker="AAPL", ts=datetime.now(tz=UTC), last=100.0, source="finnhub"
    )
    ev = aclient.post("/api/alerts/evaluate").json()
    assert ev["evaluated"] == 1 and ev["fired"] == 1 and len(sent) == 1
    st = aclient.get("/api/alerts/state").json()
    assert st[0]["state"] == "fired" and abs(st[0]["last_value"] - 30.0) < 1e-9
    hist = aclient.get("/api/alerts/history?limit=10").json()
    assert len(hist) == 1 and hist[0]["ticker"] == "AAPL" and hist[0]["fair_value"] == 130.0
    hid = hist[0]["id"]
    assert aclient.post(f"/api/alerts/history/{hid}/ack").json()["acknowledged_at"]
    assert aclient.post(f"/api/alerts/history/{hid}/snooze", json={"hours": 2}).json()["snoozed_until"]
    assert aclient.get("/api/alerts/history?unacked=true").json() == []
    assert aclient.get("/api/alerts/digest").json() == []
    assert aclient.post("/api/alerts/digest/flush").json()["items"] == 0
    assert aclient.delete(f"/api/alerts/rules/{rule['id']}").json()["deleted"] is True
    assert aclient.get("/api/alerts/state").json() == []
