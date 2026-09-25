"""ORM tables for the statements module: canonical facts, concept map, statement documents, entities.

Registered by the integrator in app/data/store/all_models.py as "app.statements.models".
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.data.store.models import utcnow
from app.data.store.sqlite import Base


class Entity(Base):
    __tablename__ = "entities"

    entity_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # cik:320193 | lei:<LEI> | tase:<id>
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    filer_type: Mapped[str] = mapped_column(
        String(20), default="unknown"
    )  # us_10k|foreign_20f|ifrs_esef|tase_only|unknown
    cik: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lei: Mapped[str | None] = mapped_column(String(20), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    fiscal_year_end_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    taxonomies: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["us-gaap"] or ["ifrs-full"]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Fact(Base):
    __tablename__ = "facts"
    __table_args__ = (
        UniqueConstraint(
            "entity_id",
            "canonical_field",
            "period_end",
            "period_type",
            "source",
            "restated_flag",
            name="uq_facts_key",
        ),
        Index("ix_facts_entity_period", "entity_id", "period_type", "period_end"),
        Index("ix_facts_doc", "doc_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    canonical_field: Mapped[str] = mapped_column(String(40))
    period_end: Mapped[date] = mapped_column(Date)
    period_type: Mapped[str] = mapped_column(String(4))  # FY | Q | H | TTM
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fiscal_period: Mapped[str | None] = mapped_column(String(4), nullable=True)  # FY, Q1..Q4, H1, H2, TTM
    value: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    unit_scale: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(
        String(20)
    )  # edgar_xbrl | xbrl_org | 6k_parsed | 6k_llm | pdf_llm | manual | derived
    source_concept: Mapped[str | None] = mapped_column(String(300), nullable=True)
    accession_or_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    restated_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
    doc_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # statement_docs.id for 6-K / PDF facts
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ConceptMap(Base):
    __tablename__ = "concept_map"
    __table_args__ = (
        UniqueConstraint(
            "source_taxonomy", "source_concept", "canonical_field", "entity_override", name="uq_concept_map"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_taxonomy: Mapped[str] = mapped_column(String(20), index=True)  # us-gaap | ifrs-full | dei | label
    source_concept: Mapped[str] = mapped_column(
        String(300)
    )  # XBRL local name, or "IS|<regex>" for label rows
    canonical_field: Mapped[str] = mapped_column(String(40), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=1)
    sign: Mapped[int] = mapped_column(Integer, default=1)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    entity_override: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class StatementDoc(Base):
    __tablename__ = "statement_docs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(8))  # pdf | 6k
    path_or_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(12), default="pending", index=True
    )  # pending|approved|rejected|failed
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    period_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="pdf_llm")
    extracted: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    validation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    llm_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
