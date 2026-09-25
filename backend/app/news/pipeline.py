"""Ingest pipeline: RawNewsItem -> raw_items (idempotent on (source, external_id)) -> item_tickers (entity
linking) -> filings upsert -> cluster assignment (url hash / fuzzy title / SimHash within 48 h among items that
share a ticker) -> stage-1 heuristics -> importance rescoring of touched clusters."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.news import dedup, importance
from app.news.enrich import heuristic
from app.news.entity import EntityMatcher
from app.news.models import ClusterItem, Filing, ItemTicker, RawItem, StoryCluster
from app.news.sources.base import NewsSource, RawNewsItem, utcnow

log = logging.getLogger(__name__)
CLUSTER_WINDOW = timedelta(hours=48)
KIND_RANK = {"filing": 0, "wire": 1, "news_api": 2, "rss": 3, "social": 4}


@dataclass
class IngestStats:
    fetched: int = 0
    inserted: int = 0
    duplicates: int = 0
    unmatched: int = 0
    filings: int = 0
    clusters_new: int = 0
    clusters_joined: int = 0
    touched: set[int] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {k: (len(v) if isinstance(v, set) else v) for k, v in self.__dict__.items()}


def _kind_rank(kind: str) -> int:
    return KIND_RANK.get(kind, 5)


async def ingest(
    source: NewsSource,
    items: list[RawNewsItem],
    matcher: EntityMatcher,
    kinds_by_source: dict[str, str] | None = None,
) -> IngestStats:
    st = IngestStats(fetched=len(items))
    kinds_by_source = kinds_by_source or {}
    for it in items:
        try:
            await _ingest_one(source, it, matcher, st, kinds_by_source)
        except Exception as e:  # one bad item must not kill the batch
            log.warning("ingest %s/%s failed: %s", source.id, it.external_id[:60], e)
    if st.touched:
        weights = await importance.load_weights()
        await rescore(sorted(st.touched), weights)
    return st


async def _ingest_one(
    source: NewsSource,
    it: RawNewsItem,
    matcher: EntityMatcher,
    st: IngestStats,
    kinds_by_source: dict[str, str],
) -> None:
    text = f"{it.title} {it.snippet or ''}"
    links = matcher.link(text, it.tickers if getattr(source, "trust_provider_tickers", True) else None)
    if not links and not source.keep_unmatched:
        st.unmatched += 1
        return
    canon = dedup.canonical_url(it.url)
    uhash = dedup.url_hash(it.url)
    tnorm = dedup.normalize_title(it.title, it.publisher)
    sh = dedup.simhash64(f"{it.title} {dedup.strip_html(it.snippet)[:300]}")
    lang = it.lang if it.lang != "en" else dedup.detect_lang(it.title)
    filing_form = (it.filing or {}).get("form")
    filing_items = (it.filing or {}).get("items")
    sent, ev = heuristic(it.title, dedup.strip_html(it.snippet), filing_form, filing_items)
    if it.sentiment is not None:
        sent = round(0.5 * sent + 0.5 * max(-1.0, min(1.0, it.sentiment)), 3)
    published = it.published_at.replace(tzinfo=None) if it.published_at.tzinfo else it.published_at
    async with session_scope_() as s:
        exists = (
            await s.execute(
                select(RawItem.id).where(
                    RawItem.source_id == source.id, RawItem.external_id == it.external_id
                )
            )
        ).scalar_one_or_none()
        if exists is not None:
            st.duplicates += 1
            return
        row = RawItem(
            source_id=source.id,
            external_id=it.external_id,
            url=it.url,
            canonical_url=canon,
            url_hash=uhash,
            title=it.title,
            title_norm=tnorm,
            snippet=dedup.strip_html(it.snippet)[:2000] or None,
            body=it.body,
            lang=lang,
            published_at=published,
            fetched_at=utcnow(),
            publisher=it.publisher,
            simhash64=dedup.to_signed64(sh),
            sentiment=sent,
            event_type=ev,
            raw=dict(it.raw),
        )
        s.add(row)
        await s.flush()
        for t, (rel, method) in links.items():
            s.add(ItemTicker(item_id=row.id, ticker=t, relevance=rel, method=method))
        if it.filing:
            await _upsert_filing(s, it.filing, row.id)
            st.filings += 1
        # a filing is its own story: two Form 4s titled "Form 4 insider transaction — X" are separate trades
        cid, method, score = (
            (None, "", 0.0)
            if it.filing
            else await _assign_cluster(s, row, set(links), source.kind, kinds_by_source)
        )
        if cid is None:
            c = StoryCluster(
                canonical_item_id=row.id,
                kind=source.kind,
                lang=lang,
                first_seen=published,
                last_seen=published,
                n_items=1,
                n_publishers=1,
                sentiment=sent,
                event_type=ev,
            )
            s.add(c)
            await s.flush()
            s.add(ClusterItem(cluster_id=c.id, item_id=row.id, sim_method="new", score=1.0))
            st.clusters_new += 1
            st.touched.add(c.id)
        else:
            s.add(ClusterItem(cluster_id=cid, item_id=row.id, sim_method=method, score=score))
            await _refresh_cluster(s, cid, kinds_by_source)
            st.clusters_joined += 1
            st.touched.add(cid)
        st.inserted += 1


def session_scope_():
    from app.data.store.sqlite import session_scope

    return session_scope()


async def _upsert_filing(s, f: dict, item_id: int) -> None:
    row = await s.get(Filing, f["accession"])
    vals = {
        "cik": f.get("cik"),
        "ticker": f.get("ticker", ""),
        "form": f.get("form", ""),
        "items": list(f.get("items") or []),
        "filed_at": f.get("filed_at"),
        "accepted_at": f.get("accepted_at"),
        "primary_doc_url": f.get("primary_doc_url", ""),
        "size": f.get("size"),
        "item_id": item_id,
    }
    if row is None:
        s.add(Filing(accession=f["accession"], **vals))
    else:
        for k, v in vals.items():
            if v is not None:
                setattr(row, k, v)


async def _assign_cluster(
    s, row: RawItem, tickers: set[str], kind: str, kinds_by_source: dict[str, str]
) -> tuple[int | None, str, float]:
    """Return (cluster_id, method, score) of an existing cluster this item belongs to, or (None, ...)."""
    # gate 1: exact canonical URL
    if row.url_hash:
        hit = (
            await s.execute(
                select(ClusterItem.cluster_id)
                .join(RawItem, RawItem.id == ClusterItem.item_id)
                .where(RawItem.url_hash == row.url_hash, RawItem.id != row.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if hit is not None:
            return int(hit), "url", 1.0
    # candidates: items within +-48h that share a ticker (or, for unmatched items, same source window)
    lo, hi = row.published_at - CLUSTER_WINDOW, row.published_at + CLUSTER_WINDOW
    stmt = (
        select(RawItem.id, RawItem.title_norm, RawItem.simhash64, ClusterItem.cluster_id)
        .join(ClusterItem, ClusterItem.item_id == RawItem.id)
        .where(RawItem.published_at >= lo, RawItem.published_at <= hi, RawItem.id != row.id)
    )
    if tickers:
        stmt = stmt.where(
            RawItem.id.in_(select(ItemTicker.item_id).where(ItemTicker.ticker.in_(sorted(tickers))))
        )
    else:
        stmt = stmt.where(RawItem.source_id == row.source_id)
    cands = (await s.execute(stmt.order_by(RawItem.published_at.desc()).limit(400))).all()
    best: tuple[int | None, str, float] = (None, "", 0.0)
    my_sh = dedup.from_signed64(row.simhash64)
    for _, tnorm, sh, cluster_id in cands:
        # gate 2: fuzzy title
        if row.title_norm and tnorm:
            sim = dedup.title_similarity(row.title_norm, tnorm)
            if sim >= dedup.TITLE_THRESHOLD and sim > best[2]:
                best = (int(cluster_id), "title", sim / 100.0)
                continue
        # gate 3: simhash
        if sh and dedup.simhash_near(my_sh, dedup.from_signed64(sh)):
            score = 1.0 - dedup.hamming(my_sh, dedup.from_signed64(sh)) / 64.0
            if best[0] is None or (best[1] == "simhash" and score > best[2]):
                best = (int(cluster_id), "simhash", round(score, 3))
    return best


async def _refresh_cluster(s, cid: int, kinds_by_source: dict[str, str]) -> None:
    c = await s.get(StoryCluster, cid)
    if c is None:
        return
    rows = (
        (
            await s.execute(
                select(RawItem)
                .join(ClusterItem, ClusterItem.item_id == RawItem.id)
                .where(ClusterItem.cluster_id == cid)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return
    kinds = {r.id: kinds_by_source.get(r.source_id, "news") for r in rows}
    canonical = min(
        rows,
        key=lambda r: (_kind_rank(kinds[r.id]) if kinds[r.id] in ("filing", "wire") else 9, r.published_at),
    )
    c.canonical_item_id = canonical.id
    c.kind = kinds.get(canonical.id, c.kind)
    c.lang = canonical.lang
    c.first_seen = min(r.published_at for r in rows)
    c.last_seen = max(r.published_at for r in rows)
    c.n_items = len(rows)
    c.n_publishers = len({(r.publisher or r.source_id).lower() for r in rows})
    if c.enriched_at is None:
        sents = [r.sentiment for r in rows if r.sentiment is not None]
        c.sentiment = round(sum(sents) / len(sents), 3) if sents else None
        c.event_type = canonical.event_type or c.event_type


async def rescore(cluster_ids: list[int], weights: dict[str, float] | None = None, market_lookup=None) -> int:
    """Recompute heuristic importance for clusters (keeps the LLM blend when an enrichment exists)."""
    weights = weights or importance.DEFAULT_WEIGHTS
    n = 0
    async with session_scope_() as s:
        for cid in cluster_ids:
            c = await s.get(StoryCluster, cid)
            if c is None:
                continue
            rows = (
                (
                    await s.execute(
                        select(RawItem)
                        .join(ClusterItem, ClusterItem.item_id == RawItem.id)
                        .where(ClusterItem.cluster_id == cid)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                continue
            ids = [r.id for r in rows]
            tick_rows = (
                await s.execute(
                    select(ItemTicker.ticker, func.max(ItemTicker.relevance))
                    .where(ItemTicker.item_id.in_(ids))
                    .group_by(ItemTicker.ticker)
                )
            ).all()
            tickers = [t for t, _ in sorted(tick_rows, key=lambda x: -(x[1] or 0))]
            filing = (
                await s.execute(select(Filing).where(Filing.item_id.in_(ids)).limit(1))
            ).scalar_one_or_none()
            src_kinds = [(r.source_id, _source_kind(r.source_id, c.kind), r.publisher) for r in rows]
            imp, parts = importance.score_cluster(
                kind=c.kind,
                item_sources=src_kinds,
                n_publishers=c.n_publishers,
                tickers=tickers,
                first_seen=c.first_seen,
                filing_form=filing.form if filing else None,
                filing_items=list(filing.items or []) if filing else None,
                filing_size=filing.size if filing else None,
                sentiment=c.sentiment,
                weights=weights,
                market_lookup=market_lookup,
            )
            parts["heuristic"] = imp
            if c.importance_parts and "llm" in c.importance_parts and c.enriched_at is not None:
                parts["llm"] = c.importance_parts["llm"]
                imp = int(round(0.5 * imp + 0.5 * int(parts["llm"])))
            c.importance = imp
            c.importance_parts = parts
            n += 1
    return n


_SOURCE_KINDS = {
    "sec_filings": "filing",
    "wires": "wire",
    "finnhub_news": "news_api",
    "massive": "news_api",
    "alphavantage": "news_api",
    "gdelt": "news_api",
    "google_news": "rss",
    "israel_rss": "rss",
}


async def relink(matcher: EntityMatcher | None = None, trusted: dict[str, bool] | None = None) -> dict:
    """Maintenance: recompute `item_tickers` for every stored raw item with the current linker rules, then
    rescore the clusters whose items changed. Idempotent. Provider tickers are kept only for sources that tag
    entities themselves (`trust_provider_tickers`); they are reconstructed from existing 'provider' rows and,
    for Finnhub, from raw['related']. Returns before/after link counts per ticker."""
    from app.news.sources import build_sources

    matcher = matcher or await EntityMatcher.load()
    if trusted is None:
        trusted = {src.id: src.trust_provider_tickers for src in build_sources()}
    before: dict[str, int] = {}
    after: dict[str, int] = {}
    changed_items: list[int] = []
    added = removed = 0
    async with session_scope_() as s:
        items = (await s.execute(select(RawItem))).scalars().all()
        rows_by_item: dict[int, list[ItemTicker]] = {}
        for r in (await s.execute(select(ItemTicker))).scalars().all():
            rows_by_item.setdefault(r.item_id, []).append(r)
        for it in items:
            rows = rows_by_item.get(it.id, [])
            for r in rows:
                before[r.ticker] = before.get(r.ticker, 0) + 1
            provider: dict[str, float] = {}
            if trusted.get(it.source_id, True):
                provider = {r.ticker: r.relevance for r in rows if r.method == "provider"}
                related = (it.raw or {}).get("related") if isinstance(it.raw, dict) else None
                for t in str(related or "").split(","):
                    if t.strip():
                        provider.setdefault(t.strip().upper(), 0.9)
            new = matcher.link(f"{it.title} {it.snippet or ''}", provider)
            for t in new:
                after[t] = after.get(t, 0) + 1
            old = {r.ticker: r for r in rows}
            if {t: (r.relevance, r.method) for t, r in old.items()} == new:
                continue
            changed_items.append(it.id)
            for t, r in old.items():
                if t not in new:
                    await s.delete(r)
                    removed += 1
            for t, (rel, method) in new.items():
                if t in old:
                    old[t].relevance, old[t].method = rel, method
                else:
                    s.add(ItemTicker(item_id=it.id, ticker=t, relevance=rel, method=method))
                    added += 1
        touched = (
            sorted(
                {
                    int(c)
                    for c in (
                        await s.execute(
                            select(ClusterItem.cluster_id).where(ClusterItem.item_id.in_(changed_items))
                        )
                    ).scalars()
                }
            )
            if changed_items
            else []
        )
    if touched:
        await rescore(touched, await importance.load_weights())
    return {
        "items": len(items),
        "changed_items": len(changed_items),
        "links_added": added,
        "links_removed": removed,
        "clusters_rescored": len(touched),
        "links_before": dict(sorted(before.items())),
        "links_after": dict(sorted(after.items())),
    }


async def split_filing_clusters() -> dict:
    """Maintenance: clusters that hold more than one SEC filing (older ingests joined same-titled Form 4s)
    are split so every filing is its own story; news items stay with the earliest filing. Idempotent."""
    kinds = kinds_map()
    moved: list[int] = []
    touched: set[int] = set()
    async with session_scope_() as s:
        rows = (
            await s.execute(
                select(ClusterItem.cluster_id, RawItem)
                .join(RawItem, RawItem.id == ClusterItem.item_id)
                .join(Filing, Filing.item_id == RawItem.id)
                .order_by(ClusterItem.cluster_id, RawItem.published_at)
            )
        ).all()
        by_cluster: dict[int, list[RawItem]] = {}
        for cid, item in rows:
            by_cluster.setdefault(int(cid), []).append(item)
        for cid, items in by_cluster.items():
            if len(items) < 2:
                continue
            touched.add(cid)
            for item in items[1:]:
                link = (
                    await s.execute(
                        select(ClusterItem).where(
                            ClusterItem.cluster_id == cid, ClusterItem.item_id == item.id
                        )
                    )
                ).scalar_one()
                await s.delete(link)
                c = StoryCluster(
                    canonical_item_id=item.id,
                    kind="filing",
                    lang=item.lang,
                    first_seen=item.published_at,
                    last_seen=item.published_at,
                    n_items=1,
                    n_publishers=1,
                    sentiment=item.sentiment,
                    event_type=item.event_type,
                )
                s.add(c)
                await s.flush()
                s.add(ClusterItem(cluster_id=c.id, item_id=item.id, sim_method="new", score=1.0))
                touched.add(c.id)
                moved.append(item.id)
        await s.flush()
        for cid in touched:
            await _refresh_cluster(s, cid, kinds)
    if touched:
        await rescore(sorted(touched), await importance.load_weights())
    return {"filings_moved": len(moved), "clusters_touched": len(touched)}


def _source_kind(source_id: str, default: str) -> str:
    return _SOURCE_KINDS.get(source_id, default)


def kinds_map() -> dict[str, str]:
    return dict(_SOURCE_KINDS)


def window_since(hours: int = 24) -> datetime:
    return utcnow() - timedelta(hours=hours)
