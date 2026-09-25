"""ORM tables for the calendar bus: `events` (one row per scheduled release / event) and `event_values` (vintages)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.data.store.models import utcnow
from app.data.store.sqlite import Base

EVENT_KINDS = (
    "macro",
    "cb_decision",
    "cb_minutes",
    "speech",
    "earnings",
    "dividend",
    "ipo",
    "auction",
    "expiry",
    "index_rebalance",
    "election",
    "geo",
    "holiday",
    "reporting_deadline",
    "risk_window",
)
CATEGORIES = (
    "inflation",
    "labour",
    "growth",
    "rates",
    "housing",
    "trade",
    "sentiment",
    "fiscal",
    "corporate",
    "other",
)
COUNTRIES = ("US", "IL", "EA", "DE", "GB", "JP", "CN", "CA", "CH", "AU", "GLOBAL")
STATUSES = ("scheduled", "released", "revised")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("event_key", "reference_period", "release_date", name="uq_events_key_period_date"),
        Index("ix_events_release_ts", "release_ts"),
        Index("ix_events_country_release", "country", "release_ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(120), index=True)
    kind: Mapped[str] = mapped_column(String(24), default="macro", index=True)
    country: Mapped[str] = mapped_column(String(8), default="US", index=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    category: Mapped[str] = mapped_column(String(24), default="other")
    importance: Mapped[int] = mapped_column(Integer, default=1)
    release_ts: Mapped[datetime] = mapped_column()  # naive UTC
    release_date: Mapped[date] = mapped_column(Date)  # UTC date of release_ts (part of the natural key)
    end_ts: Mapped[datetime | None] = mapped_column(nullable=True)  # risk windows / multi-day meetings
    reference_period: Mapped[str] = mapped_column(String(24), default="")
    status: Mapped[str] = mapped_column(String(12), default="scheduled")
    source_provider: Mapped[str] = mapped_column(String(40), default="")
    source_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    linked_series: Mapped[list] = mapped_column(JSON, default=list)
    affected_tickers: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingest_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    values: Mapped[list[EventValue]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="EventValue.vintage_ts"
    )


class EventValue(Base):
    """One vintage of the numbers attached to an event. A new row is written whenever consensus/previous/actual change."""

    __tablename__ = "event_values"
    __table_args__ = (Index("ix_event_values_event_vintage", "event_id", "vintage_ts"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    vintage_ts: Mapped[datetime] = mapped_column(default=utcnow)
    consensus: Mapped[float | None] = mapped_column(Float, nullable=True)
    consensus_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    forecast_alt: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_revised: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(24), nullable=True)
    surprise: Mapped[float | None] = mapped_column(Float, nullable=True)
    surprise_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    surprise_z: Mapped[float | None] = mapped_column(Float, nullable=True)

    event: Mapped[Event] = relationship(back_populates="values")
