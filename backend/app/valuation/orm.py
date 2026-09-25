"""ORM tables for the valuation engine. Re-exported by `app.valuation.models` (registered in all_models.py).

Tables: assumption_sets (immutable, versioned), valuation_runs, fair_value_daily, industry_stats, peer_sets, macro_cache.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.data.store.models import utcnow
from app.data.store.sqlite import Base


class AssumptionSet(Base):
    """Immutable set of valuation assumptions. Edits create a new row with parent_id -> lineage/diff."""

    __tablename__ = "assumption_sets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    scenario: Mapped[str] = mapped_column(String(16), default="base")  # bear | base | bull | custom
    source: Mapped[str] = mapped_column(
        String(24), default="manual"
    )  # auto|manual|import_csv|import_xlsx|import_sheets|import_ginzu
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # Assumptions.model_dump(exclude_none=True)
    checksum: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ValuationRun(Base):
    __tablename__ = "valuation_runs"
    __table_args__ = (Index("ix_valuation_runs_ticker_created", "ticker", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    model_id: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(24), default="")
    scenario: Mapped[str] = mapped_column(String(16), default="base")
    assumption_set_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inputs_snapshot_id: Mapped[str] = mapped_column(String(64), default="")  # InputsSnapshot.content_hash
    value_per_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    price_ts: Mapped[datetime | None] = mapped_column(nullable=True)
    upside: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="ok")  # ok | error
    result: Mapped[dict] = mapped_column(JSON, default=dict)  # ValuationResult.model_dump()
    inputs_summary: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # small provenance summary of the snapshot
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class FairValueDaily(Base):
    """One row per (ticker, date, model, scenario). Alerts read the latest row for a reference."""

    __tablename__ = "fair_value_daily"
    __table_args__ = (
        UniqueConstraint("ticker", "date", "model", "scenario", name="uq_fair_value_daily"),
        Index("ix_fair_value_daily_ticker_model", "ticker", "model", "scenario", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(32))
    date: Mapped[date] = mapped_column()
    model: Mapped[str] = mapped_column(String(64))
    scenario: Mapped[str] = mapped_column(String(16), default="base")
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    price_ts: Mapped[datetime | None] = mapped_column(nullable=True)
    upside: Mapped[float | None] = mapped_column(Float, nullable=True)  # fraction: value/price - 1
    implied_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    assumption_set_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inputs_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    created_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class IndustryStat(Base):
    """Damodaran annual industry datasets (betas / wacc / vebitda), one row per (dataset, region, industry, as_of)."""

    __tablename__ = "industry_stats"
    __table_args__ = (UniqueConstraint("dataset", "region", "industry", "as_of", name="uq_industry_stats"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String(24), index=True)  # betas | wacc | vebitda
    region: Mapped[str] = mapped_column(String(16), default="US")
    industry: Mapped[str] = mapped_column(String(120), index=True)
    as_of: Mapped[date] = mapped_column()
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    source_url: Mapped[str] = mapped_column(String(400), default="")
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)


class PeerSet(Base):
    __tablename__ = "peer_sets"

    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    sic: Mapped[str | None] = mapped_column(String(8), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(120), nullable=True)
    peers: Mapped[list] = mapped_column(
        JSON, default=list
    )  # auto-built candidates [{ticker, source, market_cap}]
    pins: Mapped[list] = mapped_column(JSON, default=list)
    excludes: Mapped[list] = mapped_column(JSON, default=list)
    as_of: Mapped[datetime | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class MacroCache(Base):
    """Cached macro inputs (rf / ERP / CRP) with provenance: key -> value, as_of, source url."""

    __tablename__ = "valuation_macro_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[float] = mapped_column(Float)
    as_of: Mapped[date | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="")
    source_url: Mapped[str] = mapped_column(String(400), default="")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
