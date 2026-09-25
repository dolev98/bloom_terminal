from datetime import timedelta

from sqlalchemy import select

from app.data.store.sqlite import session_scope
from app.news import pipeline, service
from app.news.entity import EntityMatcher, seed_entities
from app.news.models import Filing, StoryCluster
from tests.news.conftest import FakeSource, mk


async def _setup():
    await seed_entities(
        ["AAPL", "TEVA"],
        {
            "AAPL": {"cik": 320193, "name": "Apple Inc."},
            "TEVA": {"cik": 1, "name": "Teva Pharmaceutical Industries Ltd"},
        },
    )
    await service.ensure_sources()
    return await EntityMatcher.load()


async def test_ingest_dedups_and_clusters(db, now, old):
    m = await _setup()
    src = FakeSource("google_news")
    items = [
        mk(
            "Apple beats quarterly revenue estimates on iPhone strength",
            "https://reuters.com/a?utm_source=x",
            ts=now,
            publisher="Reuters",
        ),
        mk(
            "Apple Beats Quarterly Revenue Estimates On iPhone Strength",
            "https://bloomberg.com/b",
            ts=now + timedelta(hours=1),
            publisher="Bloomberg",
        ),
        mk(
            "Apple beats quarterly revenue estimates on iPhone strength",
            "https://www.reuters.com/a?fbclid=1",
            ts=now,
            publisher="Reuters",
            external_id="dup-url",
        ),
        mk(
            "Apple beats quarterly revenue estimates on iPhone strength",
            "https://reuters.com/a",
            ts=old,
            publisher="Reuters",
            external_id="same-story-old",
        ),  # exact url -> same cluster
        mk("Teva settles opioid litigation for $4.25 billion", "https://x.com/t", ts=now, publisher="WSJ"),
        mk("Nothing about anyone", "https://x.com/n", ts=now, publisher="Blog"),
    ]
    st = await pipeline.ingest(src, items, m, pipeline.kinds_map())
    assert st.inserted == 6 and st.clusters_new == 3 and st.clusters_joined == 3
    st2 = await pipeline.ingest(src, items, m, pipeline.kinds_map())
    assert st2.inserted == 0 and st2.duplicates == 6
    feed = await service.feed(tickers=["AAPL"], since="7d")
    assert (
        len(feed) == 1
        and feed[0]["n_items"] == 4
        and feed[0]["n_publishers"] == 2
        and feed[0]["event_type"] == "earnings"
    )
    assert feed[0]["sentiment"] > 0 and {t["ticker"] for t in feed[0]["tickers"]} == {"AAPL"}
    detail = await service.cluster_detail(feed[0]["id"])
    assert {i["url"] for i in detail["items"]} >= {"https://bloomberg.com/b"} and sorted(
        {ci for ci in detail["sources"]}
    ) == ["google_news"]
    unmatched = await pipeline.ingest(
        FakeSource("wires", "wire", keep_unmatched=False),
        [mk("Nothing", "https://x.com/z", sid="wires")],
        m,
        pipeline.kinds_map(),
    )
    assert unmatched.unmatched == 1 and unmatched.inserted == 0


async def test_filing_becomes_canonical_and_scores_high(db, now):
    m = await _setup()
    news = mk("Apple reports Q4 results, revenue up 8%", "https://cnbc.com/q4", ts=now, publisher="CNBC")
    filing = mk(
        "8-K Item 2.02 Results of Operations and Financial Condition — Apple Inc.",
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000101/a8k.htm",
        sid="sec_filings",
        publisher="SEC EDGAR",
        ts=now - timedelta(minutes=30),
        tickers={"AAPL": 1.0},
        external_id="0000320193-26-000101",
        filing={
            "accession": "0000320193-26-000101",
            "cik": 320193,
            "ticker": "AAPL",
            "form": "8-K",
            "items": ["2.02", "9.01"],
            "filed_at": now,
            "accepted_at": now,
            "primary_doc_url": "u",
            "size": 1,
        },
    )
    await pipeline.ingest(FakeSource("google_news"), [news], m, pipeline.kinds_map())
    st = await pipeline.ingest(FakeSource("sec_filings", "filing"), [filing], m, pipeline.kinds_map())
    assert st.filings == 1 and st.clusters_new == 1  # different title -> own cluster
    feed = await service.feed(kinds=["filing"], since="7d")
    assert feed[0]["kind"] == "filing" and feed[0]["filing"]["form"] == "8-K" and feed[0]["importance"] >= 40
    assert feed[0]["importance"] > (await service.feed(kinds=["rss"], since="7d"))[0]["importance"]
    async with session_scope() as s:
        assert (await s.get(Filing, "0000320193-26-000101")).items == ["2.02", "9.01"]
    fl = await service.filings(["AAPL"], ["8-K"])
    assert fl[0]["accession"] == "0000320193-26-000101" and fl[0]["cluster_id"] == feed[0]["id"]


async def test_mark_read_stats_rail_and_series(db, now):
    m = await _setup()
    await pipeline.ingest(
        FakeSource("google_news"),
        [
            mk("Apple surges on record sales", "https://a/1", ts=now),
            mk("Apple falls on weak demand", "https://a/2", ts=now - timedelta(days=1)),
        ],
        m,
        pipeline.kinds_map(),
    )
    feed = await service.feed(tickers=["AAPL"], since="7d")
    assert len(feed) == 2
    assert await service.mark_read([feed[0]["id"]]) == 1
    assert len(await service.feed(tickers=["AAPL"], since="7d", unread_only=True)) == 1
    st = await service.stats("AAPL", 30)
    assert st["unread"] == 1 and sum(st["count"]) == 2 and st["sentiment"][-1] > 0 > st["sentiment"][0]
    rail = await service.rail(["AAPL", "TEVA"], hours=24 * 3)
    assert (
        rail[0]["ticker"] == "AAPL"
        and rail[0]["count"] == 2
        and rail[0]["unread"] == 1
        and rail[1]["count"] == 0
    )
    res = await service.sentiment_series("AAPL")
    assert res["rows"] == 2
    from app.data.catalog.loader import get_spec
    from app.data.series_service import read_series

    assert read_series("derived:NEWS_COUNT_AAPL")["value"].to_list() == [1.0, 1.0]
    assert (await get_spec("derived:NEWS_SENTIMENT_AAPL")).params["managed_by"] == "news"
    async with session_scope() as s:
        assert (await s.execute(select(StoryCluster))).scalars().first().importance_parts["heuristic"] >= 0


async def test_untrusted_query_hints_and_relink(db, now):
    m = await _setup()
    picking = mk(
        "Apple Picking And Fall Color Offer Easy Autumn Trips From Hickory",
        "https://whky.com/p",
        publisher="WHKY",
        ts=now,
        tickers={"AAPL": 0.6},
    )
    sued = mk(
        "Apple sued in Oregon over alleged AirTag stalking",
        "https://kptv.com/s",
        ts=now,
        tickers={"AAPL": 0.6},
    )
    tip = mk(
        "One Tech Tip: iOS 27 comes with revamped screen time controls",
        "https://ap/t",
        ts=now,
        tickers={"AAPL": 0.6},
    )
    # a trusted adapter (Finnhub-like) keeps its tickers even when the text never names the company
    fh = mk("One number in the fine print", "https://fh/1", sid="finnhub_news", ts=now, tickers={"AAPL": 0.9})

    # 1) the old behaviour stored the query hint for every Google News result (simulate with a trusted fake)
    trusting = FakeSource("google_news")
    await pipeline.ingest(trusting, [picking, sued, tip], m, pipeline.kinds_map())
    await pipeline.ingest(FakeSource("finnhub_news", "news_api"), [fh], m, pipeline.kinds_map())
    assert len(await service.feed(tickers=["AAPL"], since="7d")) == 4

    # 2) relink with the real trust flags removes the hint-only / common-word links
    res = await pipeline.relink(m)
    assert res["links_before"]["AAPL"] == 4 and res["links_after"]["AAPL"] == 2 and res["links_removed"] == 2
    titles = {c["title"] for c in await service.feed(tickers=["AAPL"], since="7d")}
    assert titles == {"Apple sued in Oregon over alleged AirTag stalking", "One number in the fine print"}
    assert (await pipeline.relink(m))["changed_items"] == 0  # idempotent

    # 3) new ingests from an untrusted source ignore the hint
    hinted = FakeSource("google_news")
    hinted.trust_provider_tickers = False
    st = await pipeline.ingest(
        hinted,
        [mk("Jackson Apple Festival opens for the season", "https://j/1", ts=now, tickers={"AAPL": 0.6})],
        m,
        pipeline.kinds_map(),
    )
    assert st.inserted == 1 and len(await service.feed(tickers=["AAPL"], since="7d")) == 2


def _form4(acc: str, ts):
    return mk(
        "Form 4 insider transaction — NVIDIA CORP",
        f"https://www.sec.gov/Archives/{acc}.xml",
        sid="sec_filings",
        publisher="SEC EDGAR",
        ts=ts,
        tickers={"NVDA": 1.0},
        external_id=acc,
        filing={"accession": acc, "cik": 1045810, "ticker": "NVDA", "form": "4", "items": [], "filed_at": ts},
    )


async def test_each_filing_is_its_own_story(db, now):
    m = await _setup()
    src = FakeSource("sec_filings", "filing")
    st = await pipeline.ingest(
        src, [_form4("a-1", now - timedelta(days=1)), _form4("a-2", now)], m, pipeline.kinds_map()
    )
    assert st.clusters_new == 2
    assert len(await service.feed(kinds=["filing"], since="7d")) == 2


async def test_split_filing_clusters_repairs_old_merges(db, now):
    m = await _setup()
    src = FakeSource("sec_filings", "filing")
    await pipeline.ingest(src, [_form4("b-1", now - timedelta(days=1))], m, pipeline.kinds_map())
    # simulate the old behaviour: a second filing joined the first one's cluster
    async with session_scope() as s:
        cid = (await s.execute(select(StoryCluster.id))).scalar_one()
    await pipeline.ingest(src, [_form4("b-2", now)], m, pipeline.kinds_map())
    async with session_scope() as s:
        from app.news.models import ClusterItem

        links = (await s.execute(select(ClusterItem))).scalars().all()
        other = next(link for link in links if link.cluster_id != cid)
        orphan = await s.get(StoryCluster, other.cluster_id)
        other.cluster_id = cid
        await s.delete(orphan)
    res = await pipeline.split_filing_clusters()
    assert res["filings_moved"] == 1
    feed = await service.feed(kinds=["filing"], since="7d")
    assert len(feed) == 2 and all(c["n_items"] == 1 for c in feed)
    assert (await pipeline.split_filing_clusters())["filings_moved"] == 0
