"""ORM: user overrides of the country/indicator -> series mapping (catalog.yaml is the default)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.data.store.models import utcnow
from app.data.store.sqlite import Base


class MacroMappingOverride(Base):
    __tablename__ = "macro_mapping_overrides"
    __table_args__ = (UniqueConstraint("country", "indicator", name="uq_macro_override"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    country: Mapped[str] = mapped_column(String(8), index=True)
    indicator: Mapped[str] = mapped_column(String(40))
    series_id: Mapped[str] = mapped_column(String(200))
    transform: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
