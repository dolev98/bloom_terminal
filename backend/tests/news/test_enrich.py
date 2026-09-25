import pytest

from app.core.budget import BudgetExceeded
from app.llm import client as llm_client
from app.news import enrich, pipeline, service
from app.news.entity import EntityMatcher, seed_entities
from tests.news.conftest import FakeSource, mk


async def _cluster(now):
    await seed_entities(["AAPL"], {"AAPL": {"cik": 1, "name": "Apple Inc."}})
    await service.ensure_sources()
    m = await EntityMatcher.load()
    await pipeline.ingest(
        FakeSource("google_news"),
        [mk("Apple beats estimates, raises guidance", "https://r/1", ts=now, snippet="EPS $2.10 vs $1.95")],
        m,
        pipeline.kinds_map(),
    )
    return (await service.feed(since="7d"))[0]


def test_heuristics():
    s, ev = enrich.heuristic("Apple beats estimates, raises guidance")
    assert s > 0 and ev == "earnings"
    s2, ev2 = enrich.heuristic("Regulator opens probe into Apple; shares fall")
    assert s2 < 0 and ev2 == "lawsuit"
    assert enrich.heuristic("not strong quarter")[0] < 0
    assert enrich.heuristic("x", filing_form="8-K", filing_items=["5.02"])[1] == "management"
    assert enrich.heuristic("טבע צנחה אחרי אזהרת רווח")[0] < 0


async def test_enrich_with_mocked_llm_and_cache(db, now, monkeypatch):
    c = await _cluster(now)
    calls = []

    async def fake_parse(schema, system, content, **kw):
        calls.append(content)
        return schema(
            summary="Apple beat and raised.",
            sentiment=0.7,
            importance=80,
            event_type="earnings",
            facts=["EPS $2.10 vs $1.95"],
            why_it_matters="Guidance up.",
        ), {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.001}

    monkeypatch.setattr(llm_client, "parse", fake_parse)
    out = await enrich.enrich_cluster(c["id"])
    assert out["importance"] == 80 and "EPS $2.10" in calls[0] and "AAPL" in calls[0]
    d = await service.cluster_detail(c["id"])
    assert (
        d["summary"] == "Apple beat and raised."
        and d["enriched_at"]
        and d["prompt_version"] == enrich.PROMPT_VERSION
    )
    assert d["importance"] == round(0.5 * c["importance"] + 0.5 * 80) and d["importance_parts"]["llm"] == 80
    await enrich.enrich_cluster(c["id"])  # cached by content hash -> no second call
    assert len(calls) == 1
    await enrich.enrich_cluster(c["id"], force=True)
    assert len(calls) == 2
    assert await enrich.pending_cluster_ids(60) == []


@pytest.mark.parametrize(
    "exc", [BudgetExceeded("over"), llm_client.LLMNotConfigured("no key"), RuntimeError("net")]
)
async def test_enrich_skips_quietly(db, now, monkeypatch, exc):
    c = await _cluster(now)

    async def boom(*a, **k):
        raise exc

    monkeypatch.setattr(llm_client, "parse", boom)
    assert await enrich.enrich_cluster(c["id"]) is None
    assert (await service.cluster_detail(c["id"]))["enriched_at"] is None
