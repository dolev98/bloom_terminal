"""ORM table for the pair catalog (seeded + user-defined correlation pairs)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.store.models import utcnow
from app.data.store.sqlite import Base


class Pair(Base):
    __tablename__ = "pairs"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    a: Mapped[str] = mapped_column(String(200), index=True)
    b: Mapped[str] = mapped_column(String(200), index=True)
    transform_a: Mapped[str | None] = mapped_column(String(20), nullable=True)  # None = series default
    transform_b: Mapped[str | None] = mapped_column(String(20), nullable=True)
    freq: Mapped[str] = mapped_column(String(10), default="auto")
    lag_b: Mapped[int] = mapped_column(Integer, default=0)
    rationale: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    is_seed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "a": self.a,
            "b": self.b,
            "transform_a": self.transform_a,
            "transform_b": self.transform_b,
            "freq": self.freq,
            "lag_b": self.lag_b,
            "rationale": self.rationale,
            "tags": list(self.tags or []),
            "country": self.country,
            "is_seed": self.is_seed,
            "created_at": self.created_at,
        }
