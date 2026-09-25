from datetime import datetime

from app.news.importance import DEFAULT_WEIGHTS, filing_weight, score_cluster, source_tier


def _score(**kw):
    base = dict(
        kind="rss",
        item_sources=[("google_news", "rss", "Some Blog")],
        n_publishers=1,
        tickers=["AAPL"],
        first_seen=datetime(2026, 9, 23),
        market_lookup=lambda t, d: {"score": 0.0, "available": False},
    )
    base.update(kw)
    return score_cluster(**base)


def test_ordering_filing_gt_wire_gt_blog():
    blog, _ = _score()
    wire, _ = _score(kind="wire", item_sources=[("wires", "wire", "GlobeNewswire")])
    filing, parts = _score(
        kind="filing",
        item_sources=[("sec_filings", "filing", "SEC EDGAR")],
        filing_form="8-K",
        filing_items=["2.02"],
    )
    assert blog < wire < filing and parts["filing"] == 1.0 and parts["weights"] == DEFAULT_WEIGHTS


def test_coverage_market_and_sentiment_raise_score():
    lone, _ = _score()
    wide, p = _score(
        n_publishers=8, item_sources=[("google_news", "rss", "Reuters"), ("google_news", "rss", "Bloomberg")]
    )
    assert wide > lone and p["coverage"] == 1.0 and p["tier"] == 0.7
    moved, p2 = _score(market_lookup=lambda t, d: {"score": 0.9, "available": True, "ret_z": 4.0})
    assert moved > lone and p2["market_detail"]["ret_z"] == 4.0
    assert _score(sentiment=-0.9)[0] > lone


def test_filing_and_tier_tables():
    assert (
        filing_weight("8-K", ["7.01"]) == 0.5
        and filing_weight("SC 13D", []) == 1.0
        and filing_weight("4", [], 50000) > 0.4
    )
    assert (
        source_tier("news_api", "Reuters") == 0.7
        and source_tier("rss", "random") == 0.4
        and source_tier("wire", None) == 1.0
    )


def test_custom_weights_normalised():
    imp, parts = _score(
        weights={"coverage": 0, "tier": 0, "market": 0, "filing": 0, "sentiment": 50}, sentiment=1.0
    )
    assert imp == 100 and parts["sentiment"] == 1.0
