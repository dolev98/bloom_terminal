"""News service: source registry (rows + adapters), polling with per-source cadences, feed/cluster queries,
filings, per-ticker stats, derived sentiment/count series, read marks."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import polars as pl
from sqlalchemy import and_, desc, func, or_, select

from app.data.catalog.loader import upsert_spec
from app.data.providers.base import SeriesSpec
from app.data.registry import GreySourceDisabled, get_registry
from app.data.series_service import write_manual
from app.data.store.sqlite import session_scope
from app.news import pipeline
from app.news.entity import EntityMatcher, seed_entities
from app.news.models import ClusterItem, Entity, Filing, ItemTicker, RawItem, StoryCluster
from app.news.models import NewsSource as NewsSourceRow
from app.news.sources import NewsSource, build_sources, load_sources_yaml
from app.news.sources.base import utcnow

log = logging.getLogger(__name__)
_sources: dict[str, NewsSource] | None = None
POLL_LOOKBACK_H = 48


# --- sources --------------------------------------------------------------------------------------------
def get_sources() -> dict[str, NewsSource]:
    """Adapter instances (also registered in the provider registry so calls get rate-limit + fetch-log)."""
    global _sources
    if _sources is None:
        _sources = {s.id: s for s in build_sources()}
    reg = get_registry()
    for s in _sources.values():
        try:
            reg.get(s.id)
        except Exception:
            reg.register(s)
    return _sources


def set_sources(sources: list[NewsSource] | None) -> None:
    global _sources
    _sources = {s.id: s for s in sources} if sources is not None else None


async def ensure_sources() -> int:
    """Seed `news_sources` rows from seeds/sources.yaml (adapters section). Keeps user enabled/disabled flags."""
    cfg = load_sources_yaml()
    adapters = {a["id"]: a for a in cfg.get("adapters", [])}
    inserted = 0
    async with session_scope() as s:
        have = {r.id: r for r in (await s.execute(select(NewsSourceRow))).scalars().all()}
        for sid, src in get_sources().items():
            a = adapters.get(sid, {})
            row = have.get(sid)
            if row is None:
                s.add(
                    NewsSourceRow(
                        id=sid,
                        kind=a.get("kind", src.kind),
                        name=a.get("name", src.name),
                        base_url=a.get("base_url", ""),
                        cadence_s=int(a.get("cadence_s", src.cadence_s)),
                        license_note=a.get("license"),
                        grey=bool(a.get("grey", src.license.grey)),
                        egress=a.get("egress", src.egress),
                        lang=a.get("lang", src.lang),
                        enabled=bool(a.get("enabled", True)),
                        meta={"requires": list(src.requires)},
                    )
                )
                inserted += 1
            else:
                row.kind = a.get("kind", src.kind)
                row.name = a.get("name", src.name)
                row.grey = bool(a.get("grey", src.license.grey))
                row.egress = a.get("egress", src.egress)
    return inserted


async def sources_status() -> list[dict]:
    await ensure_sources()
    reg = get_registry()
    srcs = get_sources()
    async with session_scope() as s:
        rows = (await s.execute(select(NewsSourceRow).order_by(NewsSourceRow.id))).scalars().all()
        counts = dict(
            (await s.execute(select(RawItem.source_id, func.count()).group_by(RawItem.source_id))).all()
        )
    out = []
    for r in rows:
        src = srcs.get(r.id)
        usable, why = reg.is_usable(src) if src else (False, "no adapter")
        out.append(
            {
                "id": r.id,
                "kind": r.kind,
                "name": r.name,
                "base_url": r.base_url,
                "cadence_s": r.cadence_s,
                "license_note": r.license_note,
                "grey": r.grey,
                "egress": r.egress,
                "lang": r.lang,
                "enabled": r.enabled,
                "usable": usable,
                "reason": why,
                "requires": list(src.requires) if src else [],
                "last_fetch_at": r.last_fetch_at,
                "last_status": r.last_status,
                "last_items": r.last_items,
                "items_total": int(counts.get(r.id, 0)),
                "errors": list(src.last_errors[-5:]) if src else [],
            }
        )
    return out


async def set_source_enabled(source_id: str, enabled: bool) -> dict | None:
    async with session_scope() as s:
        row = await s.get(NewsSourceRow, source_id)
        if row is None:
            return None
        row.enabled = enabled
        return {"id": row.id, "enabled": row.enabled}


# --- entities -------------------------------------------------------------------------------------------
async def watchlist_tickers() -> list[str]:
    from app.api.routers.watchlists import all_tickers

    return await all_tickers()


async def ensure_entities(tickers: list[str]) -> dict:
    tmap: dict[str, dict] = {}
    reg = get_registry()
    try:
        edgar = reg.get("edgar")
        if reg.is_usable(edgar)[0]:
            tmap = await reg.call(edgar, "ticker_map", edgar.ticker_map, key="company_tickers")
    except Exception as e:
        log.info("entity seed without SEC names: %s", e)
    return await seed_entities(tickers, tmap)


async def entity_names(tickers: list[str]) -> dict[str, str]:
    async with session_scope() as s:
        rows = (
            (await s.execute(select(Entity).where(Entity.ticker.in_([t.upper() for t in tickers]))))
            .scalars()
            .all()
        )
    return {r.ticker: r.name for r in rows if r.name}


# --- polling --------------------------------------------------------------------------------------------
async def poll(tickers: list[str] | None = None, force: bool = False, only: list[str] | None = None) -> dict:
    """Run every enabled+usable source whose cadence elapsed (or all with force). Returns per-source stats."""
    tickers = [t.upper() for t in (tickers or await watchlist_tickers())]
    await ensure_sources()
    if tickers:
        await ensure_entities(tickers)
    names = await entity_names(tickers)
    matcher = await EntityMatcher.load()
    reg = get_registry()
    srcs = get_sources()
    kinds = pipeline.kinds_map()
    now = utcnow()
    results: dict[str, dict] = {}
    async with session_scope() as s:
        rows = {r.id: r for r in (await s.execute(select(NewsSourceRow))).scalars().all()}
    for sid, src in srcs.items():
        row = rows.get(sid)
        if only and sid not in only:
            continue
        if row is None or not row.enabled:
            results[sid] = {"status": "disabled"}
            continue
        if not force and row.last_fetch_at and (now - row.last_fetch_at).total_seconds() < row.cadence_s:
            results[sid] = {
                "status": "skipped",
                "next_in_s": int(row.cadence_s - (now - row.last_fetch_at).total_seconds()),
            }
            continue
        usable, why = reg.is_usable(src)
        if not usable:
            results[sid] = {"status": "unusable", "reason": why}
            await _mark(sid, now, f"unusable: {why}", 0)
            continue
        since = (
            (row.last_fetch_at - timedelta(hours=6))
            if row.last_fetch_at
            else now - timedelta(hours=POLL_LOOKBACK_H)
        )
        src.last_errors.clear()
        try:
            items = await reg.call(
                src,
                "fetch",
                lambda src=src, since=since: src.fetch(tickers, since, names),
                key=",".join(tickers[:8]),
            )
        except GreySourceDisabled as e:
            results[sid] = {"status": "grey-disabled", "reason": str(e)}
            await _mark(sid, now, "grey sources disabled", 0)
            continue
        except Exception as e:
            results[sid] = {"status": "error", "error": f"{type(e).__name__}: {e}"[:300]}
            await _mark(sid, now, f"error: {type(e).__name__}: {e}"[:300], 0)
            continue
        st = await pipeline.ingest(src, items, matcher, kinds)
        status = "ok" if not src.last_errors else f"partial: {src.last_errors[-1]}"[:300]
        await _mark(sid, now, status, st.inserted)
        results[sid] = {
            "status": "ok" if not src.last_errors else "partial",
            **st.as_dict(),
            "errors": list(src.last_errors[-3:]),
        }
    return {"tickers": tickers, "sources": results, "at": now}


async def _mark(sid: str, when: datetime, status: str, n: int) -> None:
    async with session_scope() as s:
        row = await s.get(NewsSourceRow, sid)
        if row is not None:
            row.last_fetch_at = when
            row.last_status = status
            row.last_items = n


# --- queries --------------------------------------------------------------------------------------------
def _item_dump(r: RawItem, tickers: list[dict] | None = None) -> dict:
    return {
        "id": r.id,
        "source_id": r.source_id,
        "external_id": r.external_id,
        "url": r.url,
        "canonical_url": r.canonical_url,
        "title": r.title,
        "snippet": r.snippet,
        "lang": r.lang,
        "published_at": r.published_at,
        "publisher": r.publisher,
        "sentiment": r.sentiment,
        "event_type": r.event_type,
        "tickers": tickers or [],
        "raw": r.raw,
    }


async def _cluster_dumps(s, clusters: list[StoryCluster], with_items: bool) -> list[dict]:
    if not clusters:
        return []
    cids = [c.id for c in clusters]
    links = (await s.execute(select(ClusterItem).where(ClusterItem.cluster_id.in_(cids)))).scalars().all()
    item_ids = [link.item_id for link in links]
    items = (
        {r.id: r for r in (await s.execute(select(RawItem).where(RawItem.id.in_(item_ids)))).scalars().all()}
        if item_ids
        else {}
    )
    tick = {}
    for t in (
        (await s.execute(select(ItemTicker).where(ItemTicker.item_id.in_(item_ids)))).scalars().all()
        if item_ids
        else []
    ):
        tick.setdefault(t.item_id, []).append(
            {"ticker": t.ticker, "relevance": t.relevance, "method": t.method}
        )
    filings = (
        {
            f.item_id: f
            for f in (await s.execute(select(Filing).where(Filing.item_id.in_(item_ids)))).scalars().all()
        }
        if item_ids
        else {}
    )
    by_cluster: dict[int, list[RawItem]] = {}
    for link in links:
        if link.item_id in items:
            by_cluster.setdefault(link.cluster_id, []).append(items[link.item_id])
    out = []
    for c in clusters:
        rows = sorted(by_cluster.get(c.id, []), key=lambda r: r.published_at)
        canon = items.get(c.canonical_item_id) or (rows[0] if rows else None)
        ct: dict[str, dict] = {}
        for r in rows:
            for t in tick.get(r.id, []):
                cur = ct.get(t["ticker"])
                if cur is None or t["relevance"] > cur["relevance"]:
                    ct[t["ticker"]] = t
        filing = next((filings[r.id] for r in rows if r.id in filings), None)
        d = {
            "id": c.id,
            "kind": c.kind,
            "lang": c.lang,
            "first_seen": c.first_seen,
            "last_seen": c.last_seen,
            "n_items": c.n_items,
            "n_publishers": c.n_publishers,
            "importance": c.importance,
            "importance_parts": c.importance_parts,
            "event_type": c.event_type,
            "summary": c.summary,
            "why_it_matters": c.why_it_matters,
            "sentiment": c.sentiment,
            "facts": c.facts,
            "enriched_at": c.enriched_at,
            "prompt_version": c.prompt_version,
            "read_at": c.read_at,
            "title": canon.title if canon else "",
            "url": (canon.canonical_url or canon.url) if canon else "",
            "publisher": canon.publisher if canon else None,
            "canonical_item_id": c.canonical_item_id,
            "sources": sorted({r.source_id for r in rows}),
            "publishers": sorted({r.publisher for r in rows if r.publisher}),
            "tickers": sorted(ct.values(), key=lambda t: -t["relevance"]),
            "filing": {
                "accession": filing.accession,
                "form": filing.form,
                "items": filing.items,
                "ticker": filing.ticker,
                "url": filing.primary_doc_url,
                "filed_at": filing.filed_at,
            }
            if filing
            else None,
        }
        if with_items:
            d["items"] = [_item_dump(r, tick.get(r.id)) for r in rows]
        out.append(d)
    return out


def parse_since(since: str | datetime | None, default_hours: int = 72) -> datetime:
    if since is None or since == "":
        return utcnow() - timedelta(hours=default_hours)
    if isinstance(since, datetime):
        return since.replace(tzinfo=None)
    s = str(since).strip().lower()
    if s.endswith("h") and s[:-1].isdigit():
        return utcnow() - timedelta(hours=int(s[:-1]))
    if s.endswith("d") and s[:-1].isdigit():
        return utcnow() - timedelta(days=int(s[:-1]))
    if s.endswith("m") and s[:-1].isdigit():
        return utcnow() - timedelta(minutes=int(s[:-1]))
    try:
        dt = datetime.fromisoformat(str(since).replace("Z", "+00:00"))
    except ValueError:
        return utcnow() - timedelta(hours=default_hours)
    return dt.astimezone(tz=None).replace(tzinfo=None) if dt.tzinfo else dt


async def feed(
    tickers: list[str] | None = None,
    since: str | datetime | None = None,
    min_importance: int = 0,
    kinds: list[str] | None = None,
    lang: str | None = None,
    limit: int = 100,
    q: str | None = None,
    unread_only: bool = False,
    event_types: list[str] | None = None,
) -> list[dict]:
    since_dt = parse_since(since)
    async with session_scope() as s:
        stmt = select(StoryCluster).where(
            StoryCluster.last_seen >= since_dt, StoryCluster.importance >= min_importance
        )
        if kinds:
            stmt = stmt.where(StoryCluster.kind.in_(kinds))
        if lang:
            stmt = stmt.where(StoryCluster.lang == lang)
        if unread_only:
            stmt = stmt.where(StoryCluster.read_at.is_(None))
        if event_types:
            stmt = stmt.where(StoryCluster.event_type.in_(event_types))
        if tickers:
            tk = [t.upper() for t in tickers]
            sub = (
                select(ClusterItem.cluster_id)
                .join(ItemTicker, ItemTicker.item_id == ClusterItem.item_id)
                .where(ItemTicker.ticker.in_(tk))
            )
            stmt = stmt.where(StoryCluster.id.in_(sub))
        if q:
            like = f"%{q.strip()}%"
            sub_q = (
                select(ClusterItem.cluster_id)
                .join(RawItem, RawItem.id == ClusterItem.item_id)
                .where(or_(RawItem.title.ilike(like), RawItem.snippet.ilike(like)))
            )
            stmt = stmt.where(StoryCluster.id.in_(sub_q))
        stmt = stmt.order_by(desc(StoryCluster.last_seen)).limit(limit)
        clusters = (await s.execute(stmt)).scalars().all()
        return await _cluster_dumps(s, list(clusters), with_items=False)


async def cluster_detail(cluster_id: int) -> dict | None:
    async with session_scope() as s:
        c = await s.get(StoryCluster, cluster_id)
        if c is None:
            return None
        out = await _cluster_dumps(s, [c], with_items=True)
        return out[0] if out else None


async def filings(
    tickers: list[str] | None = None,
    forms: list[str] | None = None,
    limit: int = 100,
    since: str | datetime | None = None,
) -> list[dict]:
    async with session_scope() as s:
        stmt = select(Filing)
        if tickers:
            stmt = stmt.where(Filing.ticker.in_([t.upper() for t in tickers]))
        if forms:
            stmt = stmt.where(Filing.form.in_(forms))
        if since:
            stmt = stmt.where(Filing.filed_at >= parse_since(since, 24 * 365))
        rows = (
            (await s.execute(stmt.order_by(desc(Filing.accepted_at), desc(Filing.filed_at)).limit(limit)))
            .scalars()
            .all()
        )
        cl = {}
        if rows:
            ids = [r.item_id for r in rows if r.item_id]
            for ci in (
                (await s.execute(select(ClusterItem).where(ClusterItem.item_id.in_(ids)))).scalars().all()
                if ids
                else []
            ):
                cl[ci.item_id] = ci.cluster_id
    return [
        {
            "accession": r.accession,
            "cik": r.cik,
            "ticker": r.ticker,
            "form": r.form,
            "items": r.items,
            "filed_at": r.filed_at,
            "accepted_at": r.accepted_at,
            "url": r.primary_doc_url,
            "size": r.size,
            "summary": r.summary,
            "cluster_id": cl.get(r.item_id),
        }
        for r in rows
    ]


async def mark_read(cluster_ids: list[int], read: bool = True) -> int:
    now = utcnow()
    async with session_scope() as s:
        rows = (await s.execute(select(StoryCluster).where(StoryCluster.id.in_(cluster_ids)))).scalars().all()
        for r in rows:
            r.read_at = now if read else None
        return len(rows)


# --- stats / series -------------------------------------------------------------------------------------
async def daily_stats(ticker: str, days: int = 30) -> pl.DataFrame:
    """Daily article count and mean item sentiment for one ticker (from raw_items x item_tickers)."""
    t = ticker.upper()
    since = utcnow() - timedelta(days=days)
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(RawItem.published_at, RawItem.sentiment)
                .join(ItemTicker, ItemTicker.item_id == RawItem.id)
                .where(ItemTicker.ticker == t, RawItem.published_at >= since)
            )
        ).all()
    if not rows:
        return pl.DataFrame(schema={"ts": pl.Datetime("us"), "count": pl.Int64, "sentiment": pl.Float64})
    df = pl.DataFrame({"ts": [r[0] for r in rows], "sentiment": [r[1] for r in rows]}).with_columns(
        pl.col("ts").cast(pl.Datetime("us")).dt.truncate("1d")
    )
    return (
        df.group_by("ts")
        .agg(pl.len().alias("count"), pl.col("sentiment").mean().alias("sentiment"))
        .sort("ts")
    )


async def stats(ticker: str, days: int = 30) -> dict:
    df = await daily_stats(ticker, days)
    t = ticker.upper()
    async with session_scope() as s:
        unread = (
            await s.execute(
                select(func.count(func.distinct(StoryCluster.id)))
                .select_from(StoryCluster)
                .join(ClusterItem, ClusterItem.cluster_id == StoryCluster.id)
                .join(ItemTicker, ItemTicker.item_id == ClusterItem.item_id)
                .where(
                    ItemTicker.ticker == t,
                    StoryCluster.read_at.is_(None),
                    StoryCluster.last_seen >= utcnow() - timedelta(days=days),
                )
            )
        ).scalar_one()
    return {
        "ticker": t,
        "days": days,
        "ts": [x.isoformat() for x in df["ts"].to_list()],
        "count": df["count"].to_list(),
        "sentiment": [None if v is None else round(float(v), 3) for v in df["sentiment"].to_list()],
        "unread": int(unread or 0),
        "series": {"sentiment": f"derived:NEWS_SENTIMENT_{t}", "count": f"derived:NEWS_COUNT_{t}"},
    }


async def sentiment_series(ticker: str, days: int = 365) -> dict:
    """Write derived:NEWS_SENTIMENT_<T> (daily mean, -1..1) and derived:NEWS_COUNT_<T> (articles/day) to the
    catalog + Parquet. The specs are `enabled=False` so the generic refresh job leaves them to the news job."""
    t = ticker.upper()
    df = await daily_stats(t, days)
    ids = {"sentiment": f"derived:NEWS_SENTIMENT_{t}", "count": f"derived:NEWS_COUNT_{t}"}
    for key, sid in ids.items():
        await upsert_spec(
            SeriesSpec(
                series_id=sid,
                provider="derived",
                provider_key=sid.split(":", 1)[1],
                name=f"{t} news {'sentiment (daily mean)' if key == 'sentiment' else 'article count'}",
                description="Computed nightly by the news module (news_sentiment_series_job) from linked headlines.",
                freq="1d",
                unit="" if key == "sentiment" else "articles",
                value_kind="survey" if key == "sentiment" else "count",
                default_transform="level" if key == "sentiment" else "level",
                country="IL" if t.endswith(".TA") else "US",
                category="news",
                tags=["news", t],
                plausible_min=-1.0 if key == "sentiment" else 0.0,
                plausible_max=1.0 if key == "sentiment" else None,
                params={"managed_by": "news", "ticker": t},
                enabled=False,
            )
        )
    n = 0
    if not df.is_empty():
        write_manual(
            ids["sentiment"], df.select(pl.col("ts"), pl.col("sentiment").alias("value")).drop_nulls("value")
        )
        write_manual(ids["count"], df.select(pl.col("ts"), pl.col("count").cast(pl.Float64).alias("value")))
        n = df.height
    return {"ticker": t, "rows": n, **ids}


async def write_gdelt_tone(ticker: str, name: str | None) -> dict:
    src = get_sources().get("gdelt")
    if src is None:
        return {"ticker": ticker, "rows": 0}
    reg = get_registry()
    t = ticker.upper()
    df = await reg.call(src, "tone", lambda: src.tone(t, name), key=t)  # type: ignore[attr-defined]
    sid = f"derived:GDELT_TONE_{t}"
    await upsert_spec(
        SeriesSpec(
            series_id=sid,
            provider="derived",
            provider_key=sid.split(":", 1)[1],
            name=f"{t} GDELT average tone",
            description="GDELT DOC 2.0 TimelineTone for the company name; written by news_sentiment_series_job.",
            freq="1d",
            value_kind="survey",
            default_transform="level",
            category="news",
            tags=["news", "gdelt", t],
            params={"managed_by": "news", "ticker": t},
            license_note="Source: GDELT Project",
            enabled=False,
        )
    )
    if not df.is_empty():
        write_manual(sid, df)
    return {"ticker": t, "rows": df.height, "series_id": sid}


async def rail(tickers: list[str] | None = None, hours: int = 24) -> list[dict]:
    """Left-rail data: per ticker unread cluster count, 24 h item count, hourly sentiment sparkline."""
    tickers = [t.upper() for t in (tickers or await watchlist_tickers())]
    since = utcnow() - timedelta(hours=hours)
    async with session_scope() as s:
        items = (
            await s.execute(
                select(ItemTicker.ticker, RawItem.published_at, RawItem.sentiment)
                .join(RawItem, RawItem.id == ItemTicker.item_id)
                .where(ItemTicker.ticker.in_(tickers), RawItem.published_at >= since)
            )
        ).all()
        unread_rows = (
            await s.execute(
                select(ItemTicker.ticker, func.count(func.distinct(StoryCluster.id)))
                .select_from(StoryCluster)
                .join(ClusterItem, ClusterItem.cluster_id == StoryCluster.id)
                .join(ItemTicker, ItemTicker.item_id == ClusterItem.item_id)
                .where(
                    ItemTicker.ticker.in_(tickers),
                    StoryCluster.read_at.is_(None),
                    StoryCluster.last_seen >= utcnow() - timedelta(days=7),
                )
                .group_by(ItemTicker.ticker)
            )
        ).all()
    unread = {t: int(n) for t, n in unread_rows}
    buckets: dict[str, list[list[float]]] = {t: [[] for _ in range(hours)] for t in tickers}
    counts: dict[str, int] = dict.fromkeys(tickers, 0)
    for t, ts, sent in items:
        if t not in buckets:
            continue
        idx = min(hours - 1, max(0, int((ts - since).total_seconds() // 3600)))
        counts[t] += 1
        if sent is not None:
            buckets[t][idx].append(sent)
    out = []
    for t in tickers:
        spark = [round(sum(b) / len(b), 3) if b else None for b in buckets[t]]
        vals = [v for v in spark if v is not None]
        out.append(
            {
                "ticker": t,
                "unread": unread.get(t, 0),
                "count": counts[t],
                "spark": spark,
                "sentiment": round(sum(vals) / len(vals), 3) if vals else None,
            }
        )
    return out


def _date(d: datetime | date) -> date:
    return d.date() if isinstance(d, datetime) else d


__all__ = [
    "get_sources",
    "set_sources",
    "ensure_sources",
    "sources_status",
    "set_source_enabled",
    "ensure_entities",
    "poll",
    "feed",
    "cluster_detail",
    "filings",
    "mark_read",
    "stats",
    "sentiment_series",
    "write_gdelt_tone",
    "rail",
    "parse_since",
    "and_",
]
