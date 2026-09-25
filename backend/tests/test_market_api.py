from datetime import datetime, timedelta

import polars as pl


def _seed_ohlcv(client, ticker="AAPL", n=40):
    from app.market import service

    base = datetime(2024, 1, 1)
    close = [100.0 + i for i in range(n)]
    df = pl.DataFrame(
        {
            "ts": [base + timedelta(days=i) for i in range(n)],
            "open": close,
            "high": [c + 1 for c in close],
            "low": [c - 1 for c in close],
            "close": close,
            "adj_close": [c * 0.5 for c in close],
            "volume": [1.0] * n,
        }
    )
    service.get_ohlcv_store().write(ticker, df)


def test_ohlcv_endpoint_with_indicators_and_adjusted(client):
    _seed_ohlcv(client)
    r = client.get("/api/market/ohlcv/AAPL", params={"indicators": "sma20,rsi14", "limit": 5}).json()
    assert r["n"] == 5 and "sma20" in r and "rsi14" in r and r["indicators"] == ["sma20", "rsi14"]
    adj = client.get("/api/market/ohlcv/AAPL", params={"adjusted": "true", "limit": 1}).json()
    assert abs(adj["close"][0] - r["close"][-1] * 0.5) < 1e-9
    csv = client.get("/api/market/ohlcv/AAPL/export.csv")
    assert csv.status_code == 200 and csv.text.startswith("ts,open,high,low,close")


def test_compare_rebases(client):
    _seed_ohlcv(client, "AAA")
    _seed_ohlcv(client, "BBB")
    r = client.get("/api/market/compare", params={"tickers": "AAA,BBB", "adjusted": "false"}).json()
    assert r["n"] == 40 and r["series"]["AAA"][0] == 100.0 and abs(r["series"]["BBB"][-1] - 139.0) < 1e-9


def test_watchlists_default_and_crud(client):
    wl = client.get("/api/watchlists").json()
    assert wl and wl[0]["name"] == "Main" and any(i["ticker"] == "AAPL" for i in wl[0]["items"])
    wid = wl[0]["id"]
    assert client.post(f"/api/watchlists/{wid}/items", json={"ticker": "aapl"}).status_code == 409
    r = client.post(f"/api/watchlists/{wid}/items", json={"ticker": "TSLA"})
    assert r.status_code == 200 and r.json()["added"] == "TSLA"
    assert client.delete(f"/api/watchlists/{wid}/items/TSLA").json()["removed"] == "TSLA"


def test_notes_crud_and_hebrew_search(client):
    n = client.post(
        "/api/notes",
        json={
            "title": "תזה על טבע",
            "body": 'התשואות של אג"ח ממשלתי עלו, מרווח האשראי התרחב',
            "tickers": ["teva"],
            "tags": ["thesis"],
        },
    ).json()
    assert n["tickers"] == ["TEVA"]
    hits = client.get(
        "/api/notes", params={"q": "תשוא"}
    ).json()  # substring match: trigram handles the ה prefix and ות suffix of התשואות
    assert [h["id"] for h in hits] == [n["id"]]
    assert client.get("/api/notes", params={"ticker": "TEVA"}).json()[0]["id"] == n["id"]
    upd = client.put(
        f"/api/notes/{n['id']}", json={"title": "x", "body": "nothing here", "tickers": [], "tags": []}
    ).json()
    assert upd["title"] == "x"
    assert client.get("/api/notes", params={"q": "תשוא"}).json() == []
    assert client.delete(f"/api/notes/{n['id']}").json()["deleted"]


def test_quotes_endpoint_uses_cache(client):
    from datetime import UTC

    from app.data.providers.base import Quote
    from app.market import service

    service._quotes["ZZZ"] = Quote(ticker="ZZZ", ts=datetime.now(tz=UTC), last=1.5, source="test")
    r = client.get("/api/market/quotes", params={"tickers": "ZZZ"}).json()
    assert r[0]["last"] == 1.5 and r[0]["stale"] is False
