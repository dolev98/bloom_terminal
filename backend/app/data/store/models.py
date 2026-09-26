"""ORM tables for metadata: series registry, series meta/freshness, prefs, caches, jobs, watchlists, layouts."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.data.store.sqlite import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Series(Base):
    __tablename__ = "series"

    series_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    provider_key: Mapped[str] = mapped_column(String(200))
    field_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    freq: Mapped[str] = mapped_column(String(10), default="1d")
    unit: Mapped[str] = mapped_column(String(40), default="")
    unit_mult: Mapped[int] = mapped_column(Integer, default=0)
    sa: Mapped[bool] = mapped_column(Boolean, default=False)
    value_kind: Mapped[str] = mapped_column(String(20), default="other")
    default_transform: Mapped[str] = mapped_column(String(20), default="level")
    country: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(40), default="uncategorized", index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    publication_lag_days: Mapped[int] = mapped_column(Integer, default=0)
    release_rule: Mapped[str | None] = mapped_column(String(120), nullable=True)
    supports_vintage: Mapped[bool] = mapped_column(Boolean, default=False)
    vintage_policy: Mapped[str] = mapped_column(String(12), default="none")
    plausible_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    plausible_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    license_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    fallbacks: Mapped[list] = mapped_column(JSON, default=list)
    formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    meta: Mapped[SeriesMeta | None] = relationship(back_populates="series", uselist=False, cascade="all, delete-orphan")


class SeriesMeta(Base):
    __tablename__ = "series_meta"

    series_id: Mapped[str] = mapped_column(ForeignKey("series.series_id", ondelete="CASCADE"), primary_key=True)
    first_ts: Mapped[datetime | None] = mapped_column(nullable=True)
    last_ts: Mapped[datetime | None] = mapped_column(nullable=True)
    n_obs: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), default="never")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    rows_last_fetch: Mapped[int] = mapped_column(Integer, default=0)
    active_fallback: Mapped[str | None] = mapped_column(String(200), nullable=True)

    series: Mapped[Series] = relationship(back_populates="meta")


class Pref(Base):
    __tablename__ = "prefs"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ResponseCache(Base):
    __tablename__ = "response_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), default="")
    op: Mapped[str] = mapped_column(String(40), default="")
    payload: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(80), index=True)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | error | skipped
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)


class FetchLog(Base):
    __tablename__ = "fetch_log"
    __table_args__ = (Index("ix_fetch_log_provider_ts", "provider", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    provider: Mapped[str] = mapped_column(String(40))
    op: Mapped[str] = mapped_column(String(40))
    key: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(16))  # ok | error | cached
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    items: Mapped[list[WatchlistItem]] = relationship(back_populates="watchlist", cascade="all, delete-orphan", order_by="WatchlistItem.position")


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(32))
    position: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_at: Mapped[datetime] = mapped_column(default=utcnow)

    watchlist: Mapped[Watchlist] = relationship(back_populates="items")


class Layout(Base):
    __tablename__ = "layouts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    tickers: Mapped[list] = mapped_column(JSON, default=list)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    price_at_note: Mapped[dict] = mapped_column(JSON, default=dict)  # {ticker: price}
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class QuoteRow(Base):
    """Latest quote per ticker (SQLite, not Parquet — quotes churn every few seconds)."""

    __tablename__ = "quotes"

    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    ts: Mapped[datetime] = mapped_column()
    last: Mapped[float] = mapped_column(Float)
    prev_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    source: Mapped[str] = mapped_column(String(20), default="")
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
