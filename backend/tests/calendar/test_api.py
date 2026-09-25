from datetime import date, datetime, timedelta

from app.api.routers import calendar as calendar_router
from app.calendar.bus import CalendarBus, set_bus
from app.calendar.types import NormalizedEvent
from app.main import app

if not any(getattr(r, "path", "").startswith("/api/calendar") for r in app.routes):
    from starlette.routing import Mount

    app.include_router(calendar_router.router)
    for m in [r for r in app.router.routes if isinstance(r, Mount)]:  # keep the SPA catch-all last
        app.router.routes.remove(m)
        app.router.routes.append(m)


class FakeSource:
    id, tier = "fake_official", "official"

    async def fetch(self, start, end):
        base = datetime.combine(start + timedelta(days=1), datetime.min.time()).replace(hour=12, minute=30)
        return [
            NormalizedEvent(
                event_key="us.cpi",
                kind="macro",
                country="US",
                title="CPI (MoM)",
                ts=base,
                importance=3,
                category="inflation",
                reference_period="2026-09",
                consensus=0.2,
                actual=0.3,
                previous=0.1,
                unit="%",
                source="fake_official",
                tier="official",
                linked_series=["fred:CPIAUCSL"],
            ),
            NormalizedEvent(
                event_key="il.cpi",
                kind="macro",
                country="IL",
                title="Israel CPI",
                ts=base + timedelta(days=2, hours=3),
                importance=3,
                category="inflation",
                reference_period="2026-09",
                consensus=0.2,
                source="fake_official",
                tier="official",
            ),
            NormalizedEvent(
                event_key="us.fomc",
                kind="cb_decision",
                country="US",
                title="FOMC",
                ts=base + timedelta(days=4),
                importance=3,
                category="rates",
                reference_period="x",
                source="fake_official",
                tier="official",
            ),
        ]


def test_calendar_endpoints(client):
    set_bus(CalendarBus([FakeSource()]))
    try:
        today = date.today()
        r = client.post(
            "/api/calendar/refresh",
            params={
                "scope": "all",
                "from": today.isoformat(),
                "to": (today + timedelta(days=10)).isoformat(),
            },
        )
        assert r.status_code == 200 and r.json()["inserted"] == 3 and r.json()["errors"] == {}
        ev = client.get(
            "/api/calendar/events",
            params={
                "from": today.isoformat(),
                "to": (today + timedelta(days=10)).isoformat(),
                "countries": "US,IL",
                "min_importance": 2,
            },
        ).json()
        assert ev["count"] == 3
        cpi = next(e for e in ev["items"] if e["event_key"] == "us.cpi")
        assert (
            cpi["status"] == "released"
            and cpi["values"]["actual"] == 0.3
            and abs(cpi["values"]["surprise"] - 0.1) < 1e-9
        )
        assert cpi["release_ts_il"] and cpi["release_ts_et"] and cpi["flag"] == "🇺🇸"
        only_cb = client.get(
            "/api/calendar/events",
            params={
                "from": today.isoformat(),
                "to": (today + timedelta(days=10)).isoformat(),
                "kinds": "cb_decision",
            },
        ).json()
        assert only_cb["count"] == 1
        d = client.get(f"/api/calendar/events/{cpi['id']}").json()
        assert (
            d["past_surprises"] and d["linked"][0]["series_id"] == "fred:CPIAUCSL" and "values_history" in d
        )
        wk = client.get("/api/calendar/week", params={"start": today.isoformat()}).json()
        assert len(wk["days"]) == 7 and {r["country"] for r in wk["rows"]} >= {"US"}
        cbs = client.get("/api/calendar/central-banks").json()
        assert {c["bank"] for c in cbs} >= {"fed", "ecb", "boi"} and all("days_to_go" in c for c in cbs)
        ics = client.get(
            "/api/calendar/export.ics",
            params={"from": today.isoformat(), "to": (today + timedelta(days=10)).isoformat()},
        )
        assert ics.status_code == 200 and b"BEGIN:VEVENT" in ics.content and b"CPI (MoM)" in ics.content
        # user risk window
        u = client.post(
            "/api/calendar/events",
            json={
                "title": "Budget vote risk",
                "release_ts": (today + timedelta(days=3)).isoformat() + "T08:00:00",
                "end_ts": (today + timedelta(days=5)).isoformat() + "T20:00:00",
                "kind": "risk_window",
                "country": "IL",
                "importance": 3,
                "affected_tickers": ["teva.ta"],
            },
        )
        assert (
            u.status_code == 200
            and u.json()["source_provider"] == "user"
            and u.json()["affected_tickers"] == ["TEVA.TA"]
        )
        rw = client.get("/api/calendar/risk-windows").json()
        assert len(rw) == 1 and rw[0]["kind"] == "risk_window"
        wl = client.get(
            "/api/calendar/events",
            params={
                "from": today.isoformat(),
                "to": (today + timedelta(days=10)).isoformat(),
                "watchlist_only": "true",
            },
        ).json()
        assert (
            wl["count"] == 3
        )  # user event has tickers outside the watchlist; macro events have none -> kept
        assert client.delete(f"/api/calendar/events/{u.json()['id']}").json()["deleted"] is True
        assert client.delete("/api/calendar/events/999999").status_code == 404
        assert client.post("/api/calendar/refresh", params={"scope": "bogus"}).status_code == 400
    finally:
        set_bus(None)
