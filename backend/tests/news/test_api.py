from datetime import UTC, datetime

from app.api.routers import news as news_router
from app.news import service
from tests.news.conftest import FakeSource, mk


async def _no_entities(tickers):
    from app.news.entity import seed_entities

    return await seed_entities(tickers, {})


def test_news_api_roundtrip(client, monkeypatch):
    if not getattr(client.app.state, "news_router", False):  # integrator wires this in app/main.py
        client.app.include_router(news_router.router)
        routes = client.app.router.routes
        routes.insert(0, routes.pop())  # ahead of the "/" static mount
        client.app.state.news_router = True
    now = datetime.now(UTC).replace(tzinfo=None)
    fake = FakeSource(
        "google_news",
        items=[
            mk("Apple beats estimates", "https://r/1", ts=now, tickers={"AAPL": 0.6}),
            mk("Teva wins approval", "https://r/2", ts=now, tickers={"TEVA": 0.6}),
        ],
    )
    service.set_sources([fake])
    monkeypatch.setattr(service, "ensure_entities", _no_entities)
    try:
        r = client.post("/api/news/poll", params={"tickers": "AAPL,TEVA"})
        assert r.status_code == 200 and r.json()["sources"]["google_news"]["inserted"] == 2
        feed = client.get("/api/news/feed", params={"tickers": "AAPL", "since": "24h"}).json()
        assert len(feed) == 1 and feed[0]["title"] == "Apple beats estimates"
        cid = feed[0]["id"]
        d = client.get(f"/api/news/clusters/{cid}").json()
        assert (
            d["items"][0]["url"] == "https://r/1"
            and client.get("/api/news/clusters/999999").status_code == 404
        )
        assert client.post("/api/news/read", json={"ids": [cid]}).json()["updated"] == 1
        assert (
            client.get("/api/news/feed", params={"unread_only": "true", "since": "24h"}).json()[0]["id"]
            != cid
        )
        srcs = client.get("/api/news/sources").json()
        assert any(s["id"] == "google_news" and s["last_status"] == "ok" for s in srcs)
        assert client.put("/api/news/sources/google_news", json={"enabled": False}).json()["enabled"] is False
        assert client.post("/api/news/poll").json()["sources"]["google_news"]["status"] == "disabled"
        st = client.get("/api/news/stats", params={"ticker": "TEVA"}).json()
        assert sum(st["count"]) == 1 and st["series"]["sentiment"] == "derived:NEWS_SENTIMENT_TEVA"
        rail = client.get("/api/news/rail", params={"tickers": "AAPL,TEVA"}).json()
        assert [x["count"] for x in rail] == [1, 1]
        assert client.get("/api/news/filings", params={"tickers": "AAPL"}).json() == []
        e = client.post(f"/api/news/enrich/{cid}").json()
        assert e["enriched"] is False  # no Anthropic key in tests
    finally:
        service.set_sources(None)
