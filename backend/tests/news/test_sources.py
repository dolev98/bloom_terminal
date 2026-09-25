from datetime import datetime

import respx
from httpx import Response

from app.news.sources.alphavantage import AlphaVantageNewsSource
from app.news.sources.gdelt import GdeltSource, parse_timeline
from app.news.sources.google_news import GoogleNewsSource, build_query
from app.news.sources.israel_rss import IsraelRssSource
from app.news.sources.massive import MassiveSource
from app.news.sources.sec_filings import SecFilingsSource, filing_title
from app.news.sources.wires import WiresSource
from tests.news.conftest import fixture

SINCE = datetime(2026, 9, 1)


@respx.mock
async def test_google_news_parses_publisher_and_hebrew_variant(db):
    respx.get(url__regex=r"https://news\.google\.com/rss/search\?.*ceid=US:en").mock(
        return_value=Response(200, text=fixture("google_news.xml"))
    )
    he = respx.get(url__regex=r"https://news\.google\.com/rss/search\?.*ceid=IL:he").mock(
        return_value=Response(200, text=fixture("themarker.xml"))
    )
    items = await GoogleNewsSource().fetch(["AAPL"], SINCE, {"AAPL": "Apple Inc."})
    assert build_query("AAPL", "Apple Inc.") == '"Apple" OR AAPL when:1d'
    en = [i for i in items if i.lang == "en"]
    assert (
        en[0].title == "Apple beats quarterly revenue estimates on iPhone strength"
        and en[0].publisher == "Reuters"
    )
    assert en[0].tickers == {"AAPL": 0.6} and en[0].published_at == datetime(2026, 9, 23, 13, 5)
    assert he.called and any(i.lang == "he" for i in items)


@respx.mock
async def test_wires_globenewswire_tickers_from_category(db):
    respx.get(url__regex=r"https://www\.globenewswire\.com/RssFeed/.*").mock(
        return_value=Response(200, text=fixture("globenewswire.xml"))
    )
    respx.get(url__regex=r"https://(www\.prnewswire\.com|feed\.businesswire\.com)/.*").mock(
        return_value=Response(500)
    )
    src = WiresSource()
    items = await src.fetch(["AAPL"], SINCE)
    aapl = [i for i in items if "AAPL" in i.tickers]
    assert (
        aapl
        and aapl[0].tickers["AAPL"] == 0.95
        and aapl[0].publisher == "GlobeNewswire"
        and aapl[0].raw["keyword"] == "Apple Inc."
    )
    assert src.last_errors  # failing feeds are recorded, not fatal


@respx.mock
async def test_sec_filings_items_and_archive_url(db):
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(
        return_value=Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}})
    )
    respx.get("https://data.sec.gov/submissions/CIK0000320193.json").mock(
        return_value=Response(200, text=fixture("sec_submissions.json"))
    )
    items = await SecFilingsSource().fetch(["AAPL", "TEVA.TA"], datetime(2026, 7, 10))
    forms = [i.filing["form"] for i in items]
    assert forms == ["8-K", "10-Q", "4"]  # S-8 filtered, too-old ones by `since`
    k = items[0]
    assert k.title == "8-K Item 2.02 Results of Operations and Financial Condition — Apple Inc."
    assert k.url == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000101/a8k.htm"
    assert (
        k.filing["items"] == ["2.02", "9.01"]
        and k.published_at == datetime(2026, 9, 22, 20, 31)
        and k.filing["size"] == 50000
    )
    assert filing_title("SC 13D", [], "X Corp").startswith("Schedule 13D")


@respx.mock
async def test_massive_insights_sentiment(db, settings, monkeypatch):
    monkeypatch.setattr(settings, "massive_api_key", "k", raising=False)
    route = respx.get(url__regex=r"https://api\.massive\.com/v2/reference/news.*").mock(
        return_value=Response(200, text=fixture("massive.json"))
    )
    items = await MassiveSource().fetch(["AAPL", "TEVA.TA"], SINCE)
    assert route.call_count == 1 and route.calls[0].request.url.params["ticker"] == "AAPL"
    assert items[0].sentiment == 0.6 and items[0].tickers["AAPL"] == 0.9 and items[0].publisher == "Benzinga"
    monkeypatch.setattr(settings, "massive_api_key", "", raising=False)
    assert await MassiveSource().fetch(["AAPL"], SINCE) == []


@respx.mock
async def test_alphavantage_rotates_one_ticker_per_call(db, settings, monkeypatch):
    monkeypatch.setattr(settings, "alphavantage_api_key", "k", raising=False)
    route = respx.get(url__regex=r"https://www\.alphavantage\.co/query.*").mock(
        return_value=Response(200, text=fixture("alphavantage.json"))
    )
    src = AlphaVantageNewsSource(calls_per_poll=1)
    items = await src.fetch(["AAPL", "MSFT"], SINCE)
    assert route.call_count == 1 and route.calls[0].request.url.params["tickers"] == "AAPL"
    assert (
        items[0].tickers == {"AAPL": 0.8}
        and items[0].sentiment == -0.35
        and items[0].published_at == datetime(2026, 9, 23, 10, 15)
    )
    await src.fetch(["AAPL", "MSFT"], SINCE)
    assert route.calls[1].request.url.params["tickers"] == "MSFT"


@respx.mock
async def test_israel_rss_hebrew_items(db):
    respx.get(url__regex=r"https://www\.themarker\.com/srv/.*").mock(
        return_value=Response(200, text=fixture("themarker.xml"))
    )
    respx.get(url__regex=r"https://www\.globes\.co\.il/.*").mock(return_value=Response(403))
    items = await IsraelRssSource().fetch([], SINCE)
    assert (
        items
        and items[0].lang == "he"
        and items[0].publisher == "TheMarker"
        and items[0].snippet == "החברה היכתה את התחזיות"
    )
    assert items[0].published_at == datetime(2026, 9, 23, 10, 53, 11)


@respx.mock
async def test_gdelt_articles_and_tone(db):
    respx.get(url__regex=r"https://api\.gdeltproject\.org/api/v2/doc/doc\?.*mode=ArtList.*").mock(
        return_value=Response(200, text=fixture("gdelt.json"))
    )
    respx.get(url__regex=r"https://api\.gdeltproject\.org/api/v2/doc/doc\?.*mode=TimelineTone.*").mock(
        return_value=Response(
            200,
            json={
                "timeline": [
                    {
                        "series": "Average Tone",
                        "data": [
                            {"date": "20260922T000000Z", "value": 1.5},
                            {"date": "20260923T000000Z", "value": -0.5},
                        ],
                    }
                ]
            },
        )
    )
    src = GdeltSource()
    items = await src.fetch(["AAPL"], SINCE, {"AAPL": "Apple Inc."})
    assert items[0].raw["query"] == '"Apple"' or items[0].raw["query"] == "Apple"
    assert items[0].publisher == "example.org" and items[0].tickers == {"AAPL": 0.5}
    df = await src.tone("AAPL", "Apple Inc.")
    assert df.height == 2 and df["value"].to_list() == [1.5, -0.5] and parse_timeline({}).is_empty()
