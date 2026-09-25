"""ORM tables for the news module. Registered in app/data/store/all_models.py as "app.news.models"."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.data.store.models import utcnow
from app.data.store.sqlite import Base


class NewsSource(Base):
    """One row per feed/adapter (seeded from seeds/sources.yaml + the code-defined adapters)."""

    __tablename__ = "news_sources"

    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="rss")  # filing | wire | news_api | rss | social
    name: Mapped[str] = mapped_column(String(120), default="")
    base_url: Mapped[str] = mapped_column(String(500), default="")
    cadence_s: Mapped[int] = mapped_column(Integer, default=900)
    license_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    grey: Mapped[bool] = mapped_column(Boolean, default=False)
    egress: Mapped[str] = mapped_column(String(8), default="any")
    lang: Mapped[str] = mapped_column(String(8), default="en")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fetch_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_items: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class RawItem(Base):
    __tablename__ = "raw_items"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_raw_items_source_external"),
        Index("ix_raw_items_published", "published_at"),
        Index("ix_raw_items_url_hash", "url_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("news_sources.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(Text, default="")
    canonical_url: Mapped[str] = mapped_column(Text, default="")
    url_hash: Mapped[str] = mapped_column(String(40), default="")
    title: Mapped[str] = mapped_column(Text, default="")
    title_norm: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    lang: Mapped[str] = mapped_column(String(8), default="en")
    published_at: Mapped[datetime] = mapped_column()
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)
    publisher: Mapped[str | None] = mapped_column(String(200), nullable=True)
    simhash64: Mapped[int] = mapped_column(BigInteger, default=0)  # stored signed; see dedup.to_signed64
    sentiment: Mapped[float | None] = mapped_column(Float, nullable=True)  # heuristic (or provider) -1..1
    event_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class StoryCluster(Base):
    __tablename__ = "story_clusters"
    __table_args__ = (Index("ix_story_clusters_last_seen", "last_seen"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    canonical_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_items.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16), default="news")  # kind of the canonical item's source
    lang: Mapped[str] = mapped_column(String(8), default="en")
    first_seen: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(default=utcnow)
    n_items: Mapped[int] = mapped_column(Integer, default=0)
    n_publishers: Mapped[int] = mapped_column(Integer, default=0)
    importance: Mapped[int] = mapped_column(Integer, default=0)
    importance_parts: Mapped[dict] = mapped_column(JSON, default=dict)
    event_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    why_it_matters: Mapped[str | None] = mapped_column(Text, nullable=True)
    sentiment: Mapped[float | None] = mapped_column(Float, nullable=True)
    facts: Mapped[list] = mapped_column(JSON, default=list)
    enriched_at: Mapped[datetime | None] = mapped_column(nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ClusterItem(Base):
    __tablename__ = "cluster_items"

    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("story_clusters.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_items.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    sim_method: Mapped[str] = mapped_column(String(16), default="new")  # new | url | title | simhash
    score: Mapped[float] = mapped_column(Float, default=1.0)


class ItemTicker(Base):
    __tablename__ = "item_tickers"

    item_id: Mapped[int] = mapped_column(ForeignKey("raw_items.id", ondelete="CASCADE"), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    relevance: Mapped[float] = mapped_column(Float, default=1.0)
    method: Mapped[str] = mapped_column(String(12), default="alias")  # provider | alias | cashtag | llm


class Entity(Base):
    __tablename__ = "news_entities"

    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    cik: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    exchange: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_watchlist: Mapped[bool] = mapped_column(Boolean, default=False)


class EntityAlias(Base):
    __tablename__ = "news_entity_aliases"
    __table_args__ = (UniqueConstraint("ticker", "alias", name="uq_entity_alias"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    alias: Mapped[str] = mapped_column(String(200))
    lang: Mapped[str] = mapped_column(String(8), default="en")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(20), default="sec")  # sec | seed_he | user | derived


class Filing(Base):
    __tablename__ = "news_filings"
    __table_args__ = (Index("ix_filings_ticker_filed", "ticker", "filed_at"),)

    accession: Mapped[str] = mapped_column(String(30), primary_key=True)
    cik: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ticker: Mapped[str] = mapped_column(String(32), default="")
    form: Mapped[str] = mapped_column(String(20), default="")
    items: Mapped[list] = mapped_column(JSON, default=list)  # 8-K item codes, e.g. ["2.02", "9.01"]
    filed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    primary_doc_url: Mapped[str] = mapped_column(Text, default="")
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_items.id", ondelete="SET NULL"), nullable=True
    )


class NewsEnrichment(Base):
    __tablename__ = "news_enrichments"
    __table_args__ = (Index("ix_news_enrichments_hash", "content_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cluster_id: Mapped[int | None] = mapped_column(
        ForeignKey("story_clusters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    item_id: Mapped[int | None] = mapped_column(ForeignKey("raw_items.id", ondelete="CASCADE"), nullable=True)
    model: Mapped[str] = mapped_column(String(40), default="")
    prompt_version: Mapped[str] = mapped_column(String(20), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    why_it_matters: Mapped[str | None] = mapped_column(Text, nullable=True)
    sentiment: Mapped[float | None] = mapped_column(Float, nullable=True)
    importance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    facts: Mapped[list] = mapped_column(JSON, default=list)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
