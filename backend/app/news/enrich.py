"""Two-stage enrichment. Stage 1 (free, every headline): keyword sentiment lexicon + event-type keywords.
Stage 2 (Claude Haiku via app.llm.client.parse): summary / sentiment / importance / event_type / facts /
why_it_matters for clusters above the importance threshold or filings/wires. Cached by content hash; budget and
"LLM not configured" errors are swallowed (cluster stays un-enriched)."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.budget import BudgetExceeded
from app.data.store.sqlite import session_scope
from app.llm import client as llm_client
from app.news.models import ClusterItem, ItemTicker, NewsEnrichment, RawItem, StoryCluster

log = logging.getLogger(__name__)
PROMPT_VERSION = "news-v1"
EVENT_TYPES = [
    "earnings",
    "guidance",
    "m&a",
    "offering",
    "dividend",
    "buyback",
    "rating",
    "lawsuit",
    "macro",
    "management",
    "product",
    "other",
]
EventType = Literal[
    "earnings",
    "guidance",
    "m&a",
    "offering",
    "dividend",
    "buyback",
    "rating",
    "lawsuit",
    "macro",
    "management",
    "product",
    "other",
]

POSITIVE: dict[str, float] = {
    "beat": 1.0,
    "beats": 1.0,
    "record": 0.8,
    "surge": 1.0,
    "surges": 1.0,
    "soar": 1.0,
    "soars": 1.0,
    "jump": 0.8,
    "jumps": 0.8,
    "rally": 0.8,
    "rallies": 0.8,
    "upgrade": 1.0,
    "upgraded": 1.0,
    "upgrades": 1.0,
    "outperform": 0.8,
    "raises": 0.8,
    "raise": 0.6,
    "raised": 0.8,
    "strong": 0.6,
    "growth": 0.5,
    "profit": 0.5,
    "profitable": 0.6,
    "buyback": 0.6,
    "dividend": 0.4,
    "approval": 0.8,
    "approved": 0.8,
    "wins": 0.7,
    "win": 0.6,
    "won": 0.6,
    "expands": 0.5,
    "expansion": 0.5,
    "partnership": 0.4,
    "exceeds": 0.9,
    "tops": 0.8,
    "gain": 0.6,
    "gains": 0.6,
    "higher": 0.4,
    "bullish": 0.8,
    "optimistic": 0.6,
    "buy": 0.5,
    "boost": 0.6,
    "boosts": 0.6,
    "accelerates": 0.6,
    "momentum": 0.4,
    "upside": 0.6,
    "breakthrough": 0.8,
    "acquire": 0.2,
    "acquires": 0.2,
    "עלייה": 0.6,
    "עלה": 0.5,
    "זינוק": 0.9,
    "זינקה": 0.9,
    "זינק": 0.9,
    "שיא": 0.8,
    "רווח": 0.5,
    "צמיחה": 0.5,
    "מעל": 0.3,
    "היכתה": 0.8,
    "הכתה": 0.8,
    "אישור": 0.5,
    "עסקה": 0.2,
    "דיבידנד": 0.4,
    "המלצת": 0.2,
    "קנייה": 0.5,
}
NEGATIVE: dict[str, float] = {
    "miss": 1.0,
    "misses": 1.0,
    "missed": 1.0,
    "plunge": 1.0,
    "plunges": 1.0,
    "plummet": 1.0,
    "plummets": 1.0,
    "tumble": 0.9,
    "tumbles": 0.9,
    "sink": 0.8,
    "sinks": 0.8,
    "fall": 0.6,
    "falls": 0.6,
    "fell": 0.6,
    "drop": 0.6,
    "drops": 0.6,
    "slump": 0.8,
    "slumps": 0.8,
    "downgrade": 1.0,
    "downgraded": 1.0,
    "downgrades": 1.0,
    "underperform": 0.8,
    "cut": 0.6,
    "cuts": 0.6,
    "lowers": 0.8,
    "lowered": 0.8,
    "weak": 0.6,
    "loss": 0.6,
    "losses": 0.6,
    "lawsuit": 0.7,
    "sued": 0.7,
    "probe": 0.6,
    "investigation": 0.6,
    "recall": 0.8,
    "delay": 0.5,
    "delays": 0.5,
    "delayed": 0.5,
    "layoffs": 0.6,
    "layoff": 0.6,
    "bankruptcy": 1.0,
    "default": 0.9,
    "fraud": 1.0,
    "warns": 0.7,
    "warning": 0.7,
    "decline": 0.6,
    "declines": 0.6,
    "bearish": 0.8,
    "sell": 0.5,
    "dilution": 0.6,
    "impairment": 0.7,
    "restatement": 0.9,
    "resigns": 0.5,
    "resignation": 0.5,
    "halts": 0.7,
    "halted": 0.7,
    "shortfall": 0.8,
    "disappoints": 0.9,
    "disappointing": 0.9,
    "ירידה": 0.6,
    "ירד": 0.5,
    "צניחה": 0.9,
    "צנחה": 0.9,
    "צנח": 0.9,
    "הפסד": 0.7,
    "תביעה": 0.6,
    "חקירה": 0.6,
    "פיטורים": 0.6,
    "אזהרה": 0.6,
    "אזהרת": 0.6,
    "קריסה": 1.0,
    "פשיטת רגל": 1.0,
    "מכירה": 0.2,
    "הורדת": 0.5,
    "פספסה": 0.9,
    "החמצה": 0.9,
}
NEGATORS = {"not", "no", "never", "fails", "failed", "without", "לא", "אין"}
EVENT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "earnings",
        (
            "earnings",
            "results",
            "estimates",
            "beats",
            "quarter",
            "q1",
            "q2",
            "q3",
            "q4",
            "eps",
            "revenue",
            "fiscal",
            "10-q",
            "10-k",
            "2.02",
            "דוחות",
            "רבעון",
            "הכנסות",
        ),
    ),
    (
        "guidance",
        ("guidance", "outlook", "forecast", "raises full-year", "cuts full-year", "expects", "תחזית"),
    ),
    (
        "m&a",
        (
            "acquire",
            "acquires",
            "acquisition",
            "merger",
            "merge",
            "takeover",
            "to buy",
            "buyout",
            "deal to",
            "13d",
            "רכישה",
            "מיזוג",
            "עסקת",
        ),
    ),
    (
        "offering",
        (
            "offering",
            "ipo",
            "secondary",
            "convertible",
            "notes due",
            "prices",
            "priced",
            "shelf",
            "הנפקה",
            "גיוס",
        ),
    ),
    ("dividend", ("dividend", "distribution", "דיבידנד")),
    ("buyback", ("buyback", "repurchase", "רכישה עצמית")),
    (
        "rating",
        (
            "upgrade",
            "downgrade",
            "price target",
            "initiates",
            "rating",
            "overweight",
            "underweight",
            "outperform",
            "neutral",
            "המלצה",
            "יעד",
        ),
    ),
    (
        "lawsuit",
        (
            "lawsuit",
            "class action",
            "sued",
            "settlement",
            "probe",
            "investigation",
            "antitrust",
            "ftc",
            "doj",
            "sec charges",
            "תביעה",
            "חקירה",
        ),
    ),
    (
        "management",
        (
            "ceo",
            "cfo",
            "chief",
            "appoints",
            "names",
            "resigns",
            "steps down",
            "board",
            "director",
            "5.02",
            'מנכ"ל',
            "מנכל",
            "סמנכ",
            "דירקטור",
        ),
    ),
    (
        "product",
        (
            "launch",
            "launches",
            "unveils",
            "product",
            "approval",
            "fda",
            "chip",
            "model",
            "device",
            "platform",
            "משיקה",
            "השקה",
            "מוצר",
        ),
    ),
    (
        "macro",
        (
            "fed",
            "inflation",
            "rates",
            "tariff",
            "gdp",
            "cpi",
            "central bank",
            "bank of israel",
            "בנק ישראל",
            "ריבית",
            "אינפלציה",
        ),
    ),
]
_TOKEN_RE = re.compile(r"[\w'\-]+", re.UNICODE)


def heuristic_sentiment(text: str) -> float:
    toks = [t.lower() for t in _TOKEN_RE.findall(text or "")]
    if not toks:
        return 0.0
    score = 0.0
    hits = 0
    for i, t in enumerate(toks):
        w = POSITIVE.get(t, 0.0) - NEGATIVE.get(t, 0.0)
        if w == 0.0:
            continue
        if i > 0 and toks[i - 1] in NEGATORS:
            w = -w
        score += w
        hits += 1
    if not hits:
        return 0.0
    return max(-1.0, min(1.0, score / (hits**0.5) / 1.5))


def heuristic_event_type(
    text: str, filing_form: str | None = None, filing_items: list[str] | None = None
) -> str:
    if filing_form:
        its = set(filing_items or [])
        if filing_form.startswith("8-K"):
            if "2.02" in its:
                return "earnings"
            if its & {"1.01", "2.01"}:
                return "m&a"
            if "5.02" in its:
                return "management"
            if "3.02" in its:
                return "offering"
        if filing_form in ("10-K", "10-Q", "20-F"):
            return "earnings"
        if filing_form.startswith("SC 13"):
            return "m&a"
        if filing_form == "4":
            return "management"
    low = (text or "").lower()
    for ev, kws in EVENT_KEYWORDS:
        if any(k in low for k in kws):
            return ev
    return "other"


def heuristic(
    title: str,
    snippet: str | None = None,
    filing_form: str | None = None,
    filing_items: list[str] | None = None,
) -> tuple[float, str]:
    text = f"{title} {snippet or ''}"
    return round(heuristic_sentiment(text), 3), heuristic_event_type(text, filing_form, filing_items)


# --- stage 2: Claude ------------------------------------------------------------------------------------
class EnrichmentOut(BaseModel):
    summary: str = Field(description="<= 40 words, factual, no hype")
    sentiment: float = Field(
        ge=-1, le=1, description="-1 very negative for the company's shareholders .. +1 very positive"
    )
    importance: int = Field(
        ge=0, le=100, description="0 = noise, 100 = must-know today for a holder of the stock"
    )
    event_type: EventType
    facts: list[str] = Field(
        default_factory=list, description="2-5 short verifiable facts with numbers when present"
    )
    why_it_matters: str = Field(default="", description="one sentence for an investor")


SYSTEM = (
    "You are a sell-side news desk analyst for a single-user Bloomberg-style terminal. You receive one story cluster: "
    "several headlines/snippets from different publishers about the same event, plus the linked tickers. "
    "Return a structured triage: a <= 40 word summary, sentiment for the shareholders of the linked tickers (-1..1), "
    "importance 0-100 (earnings/guidance/M&A/major regulatory/8-K 2.02 or 1.01 are >= 70; routine PR, listicles, "
    "price-move recaps and analyst chatter are <= 40), event_type from the fixed list, 2-5 concrete facts (numbers, "
    "dates, names), and one sentence on why it matters. Hebrew input is fine; always answer in English. Never invent "
    "numbers that are not in the input."
)


def _content_hash(cluster_id: int, items: list[dict]) -> str:
    return llm_client.content_hash(PROMPT_VERSION, [(i["title"], i.get("snippet") or "")[:2] for i in items])


def _blend_importance(heuristic_imp: int, llm_imp: int) -> int:
    return int(round(0.5 * heuristic_imp + 0.5 * llm_imp))


async def enrich_cluster(cluster_id: int, force: bool = False) -> dict | None:
    """Run Claude on one cluster. Returns the enrichment dict, or None when skipped (cached-and-not-forced,
    budget exhausted, LLM not configured, or the cluster is empty)."""
    async with session_scope() as s:
        c = await s.get(StoryCluster, cluster_id)
        if c is None:
            return None
        rows = (
            (
                await s.execute(
                    select(RawItem)
                    .join(ClusterItem, ClusterItem.item_id == RawItem.id)
                    .where(ClusterItem.cluster_id == cluster_id)
                    .order_by(RawItem.published_at)
                )
            )
            .scalars()
            .all()
        )
        tickers = (
            sorted(
                {
                    r.ticker
                    for r in (
                        await s.execute(
                            select(ItemTicker).where(ItemTicker.item_id.in_([r.id for r in rows]))
                        )
                    )
                    .scalars()
                    .all()
                }
            )
            if rows
            else []
        )
        items = [
            {
                "title": r.title,
                "snippet": (r.snippet or "")[:400],
                "publisher": r.publisher,
                "ts": r.published_at.isoformat(),
                "url": r.canonical_url or r.url,
                "source": r.source_id,
            }
            for r in rows[:12]
        ]
        heuristic_imp = c.importance
    if not items:
        return None
    chash = _content_hash(cluster_id, items)
    async with session_scope() as s:
        cached = (
            await s.execute(
                select(NewsEnrichment)
                .where(NewsEnrichment.content_hash == chash, NewsEnrichment.prompt_version == PROMPT_VERSION)
                .limit(1)
            )
        ).scalar_one_or_none()
        if cached and not force:
            await _apply(s, cluster_id, cached, heuristic_imp)
            return _dump(cached)
    content = "\n".join(
        [f"Tickers: {', '.join(tickers) or 'none'}", ""]
        + [
            f"[{i['source']} | {i['publisher'] or '?'} | {i['ts']}] {i['title']}"
            + (f"\n  {i['snippet']}" if i["snippet"] else "")
            for i in items
        ]
    )
    try:
        parsed, usage = await llm_client.parse(
            EnrichmentOut,
            SYSTEM,
            content,
            model=llm_client.HAIKU,
            purpose="news_enrich",
            max_tokens=700,
            estimated_usd=0.003,
            meta={"cluster_id": cluster_id},
        )
    except (BudgetExceeded, llm_client.LLMNotConfigured) as e:
        log.info("news enrich skipped for cluster %s: %s", cluster_id, e)
        return None
    except Exception as e:  # network/refusal — do not poison the cluster
        log.warning("news enrich failed for cluster %s: %s", cluster_id, e)
        return None
    async with session_scope() as s:
        row = NewsEnrichment(
            cluster_id=cluster_id,
            model=llm_client.HAIKU,
            prompt_version=PROMPT_VERSION,
            content_hash=chash,
            summary=parsed.summary,
            why_it_matters=parsed.why_it_matters,
            sentiment=float(parsed.sentiment),
            importance=int(parsed.importance),
            event_type=parsed.event_type,
            facts=list(parsed.facts),
            tokens=int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0)),
            cost_usd=float(usage.get("cost_usd", 0.0)),
        )
        s.add(row)
        await s.flush()
        await _apply(s, cluster_id, row, heuristic_imp)
        return _dump(row)


async def _apply(s, cluster_id: int, e: NewsEnrichment, heuristic_imp: int) -> None:
    c = await s.get(StoryCluster, cluster_id)
    if c is None:
        return
    c.summary = e.summary
    c.why_it_matters = e.why_it_matters
    c.sentiment = e.sentiment
    c.event_type = e.event_type or c.event_type
    c.facts = list(e.facts or [])
    if e.importance is not None:
        parts = dict(c.importance_parts or {})
        parts["heuristic"] = parts.get("heuristic", heuristic_imp)
        parts["llm"] = e.importance
        c.importance_parts = parts
        c.importance = _blend_importance(parts["heuristic"], e.importance)
    c.enriched_at = datetime.now(UTC).replace(tzinfo=None)
    c.prompt_version = e.prompt_version


def _dump(e: NewsEnrichment) -> dict:
    return {
        "id": e.id,
        "cluster_id": e.cluster_id,
        "model": e.model,
        "prompt_version": e.prompt_version,
        "summary": e.summary,
        "why_it_matters": e.why_it_matters,
        "sentiment": e.sentiment,
        "importance": e.importance,
        "event_type": e.event_type,
        "facts": e.facts,
        "tokens": e.tokens,
        "cost_usd": e.cost_usd,
        "created_at": e.created_at,
    }


async def pending_cluster_ids(threshold: int, limit: int = 20, since_hours: int = 72) -> list[int]:
    from datetime import timedelta

    from sqlalchemy import or_

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=since_hours)
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(StoryCluster.id)
                    .where(StoryCluster.enriched_at.is_(None), StoryCluster.last_seen >= cutoff)
                    .where(
                        or_(StoryCluster.importance >= threshold, StoryCluster.kind.in_(["filing", "wire"]))
                    )
                    .order_by(StoryCluster.importance.desc(), StoryCluster.last_seen.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [int(r) for r in rows]
