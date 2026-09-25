"""Normalized calendar event exchanged between sources and the bus, and the Source protocol."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Protocol, runtime_checkable

from pydantic import Field, field_validator

from app.data.providers.base import CalendarEvent

# Source tiers drive per-field priority when several sources describe the same event.
TIERS = ("official", "rule", "vendor", "forexfactory", "user")


class NormalizedEvent(CalendarEvent):
    """`CalendarEvent` (providers/base) + the fields the bus needs to dedupe and version values."""

    id: str = ""
    event_key: str
    kind: str = "macro"
    reference_period: str = ""
    currency: str | None = None
    status: str = "scheduled"
    end_ts: datetime | None = None
    consensus_source: str | None = None
    forecast_alt: float | None = None
    previous_revised: float | None = None
    actual_source: str | None = None
    notes: str | None = None
    tier: str = "vendor"
    verified: bool = True
    tags: list[str] = Field(default_factory=list)

    @field_validator("ts", "end_ts", mode="after")
    @classmethod
    def _to_naive_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is not None:
            v = v.astimezone(UTC).replace(tzinfo=None)
        return v.replace(microsecond=0)

    @property
    def release_date(self) -> date:
        return self.ts.date()

    @property
    def dedupe_key(self) -> tuple[str, str]:
        return (self.event_key, self.reference_period or self.ts.date().isoformat())


@runtime_checkable
class Source(Protocol):
    id: str
    tier: str

    async def fetch(self, start: date, end: date) -> list[NormalizedEvent]: ...


def to_utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def dedupe_events(events: Iterable[NormalizedEvent]) -> list[NormalizedEvent]:
    """Drop exact duplicates within one source's output (same dedupe key), keeping the first occurrence."""
    seen: set[tuple[str, str]] = set()
    out: list[NormalizedEvent] = []
    for e in events:
        k = e.dedupe_key
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out
