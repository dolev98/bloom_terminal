"""Core data-layer types: series specs, observations, quotes, statements, news, calendar events, and the Provider protocol.

Every datum in the terminal is a *series* (id = "provider:key[:field]") registered in the catalog.
Adding a data TYPE = a new Pydantic model + Capability flag + router.
Adding a SOURCE = one Provider subclass + registry entry.
Adding a SERIES = a catalog row (UI / YAML seed).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Flag, auto
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, Field

Freq = Literal["tick", "1m", "5m", "1h", "1d", "1w", "1mo", "1q", "1y", "irregular"]
ValueKind = Literal["price", "yield", "spread", "level_index", "flow", "stock", "ratio", "survey", "count", "other"]
Transform = Literal["level", "log_ret", "diff", "diff_bp", "pct", "yoy", "zscore"]

DEFAULT_TRANSFORM_BY_KIND: dict[str, str] = {
    "price": "log_ret",
    "level_index": "log_ret",
    "yield": "diff_bp",
    "spread": "diff_bp",
    "flow": "pct",
    "stock": "yoy",
    "ratio": "diff",
    "survey": "diff",
    "count": "pct",
    "other": "level",
}


class Capability(Flag):
    NONE = 0
    QUOTES = auto()
    OHLCV = auto()
    SERIES = auto()
    STATEMENTS = auto()
    NEWS = auto()
    CALENDAR = auto()
    EVENTS = auto()
    HOLIDAYS = auto()
    SEARCH = auto()


@dataclass(frozen=True)
class RateSpec:
    """Token-bucket parameters. Any field may be None (unlimited)."""

    per_second: float | None = None
    per_minute: float | None = None
    per_day: float | None = None
    burst: int = 1
    concurrency: int = 2


@dataclass(frozen=True)
class LicenseSpec:
    """How we are allowed to use a source. Grey sources are unofficial and gated by prefs.grey_sources_enabled."""

    grey: bool = False
    store_allowed: bool = True
    personal_use_only: bool = True
    attribution: str | None = None
    note: str | None = None


class SeriesSpec(BaseModel):
    series_id: str = Field(description="Global id, e.g. 'fred:DGS10', 'boi:EXR/RER_USD_ILS', 'yf:^GSPC:close'")
    provider: str
    provider_key: str
    field_name: str | None = None
    name: str = ""
    description: str = ""
    freq: Freq = "1d"
    unit: str = ""
    unit_mult: int = 0
    sa: bool = False
    value_kind: ValueKind = "other"
    default_transform: Transform = "level"
    country: str | None = None
    category: str = "uncategorized"
    tags: list[str] = Field(default_factory=list)
    publication_lag_days: int = 0
    release_rule: str | None = None
    supports_vintage: bool = False
    vintage_policy: Literal["none", "native", "snapshot"] = "none"
    plausible_min: float | None = None
    plausible_max: float | None = None
    license_note: str | None = None
    fallbacks: list[str] = Field(default_factory=list)
    formula: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    schema_version: int = 1

    @staticmethod
    def parse_id(series_id: str) -> tuple[str, str, str | None]:
        parts = series_id.split(":", 2)
        if len(parts) < 2:
            raise ValueError(f"bad series id {series_id!r}; expected provider:key[:field]")
        provider, key = parts[0], parts[1]
        fld = parts[2] if len(parts) == 3 else None
        return provider, key, fld


class Quote(BaseModel):
    ticker: str
    ts: datetime
    last: float
    source: str
    bid: float | None = None
    ask: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    prev_close: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    currency: str = "USD"


class Statement(BaseModel):
    entity_id: str
    ticker: str | None = None
    period_end: date
    period_type: Literal["FY", "Q", "H", "TTM"]
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    currency: str = "USD"
    unit_scale: int = 0
    source: str
    accession_or_url: str | None = None
    filed_at: date | None = None
    confidence: float = 1.0
    items: dict[str, float] = Field(default_factory=dict)


class NewsItem(BaseModel):
    id: str
    source: str
    ts: datetime
    title: str
    url: str
    tickers: list[str] = Field(default_factory=list)
    publisher: str | None = None
    summary: str | None = None
    body: str | None = None
    lang: str = "en"
    sentiment: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class CalendarEvent(BaseModel):
    id: str
    kind: str = "macro"  # macro | cb_decision | earnings | dividend | ipo | auction | expiry | holiday | geo | risk_window
    country: str
    title: str
    ts: datetime
    importance: int = 1
    category: str | None = None
    actual: float | None = None
    consensus: float | None = None
    previous: float | None = None
    unit: str | None = None
    source: str
    source_url: str | None = None
    linked_series: list[str] = Field(default_factory=list)
    affected_tickers: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class CorporateEvent(BaseModel):
    id: str
    ticker: str
    kind: str  # earnings | dividend | split | ipo | agm | filing_deadline
    ts: datetime
    source: str
    details: dict[str, Any] = Field(default_factory=dict)


class Holiday(BaseModel):
    date: date
    name: str
    country: str
    exchange_closed: bool = False
    source: str


OBS_SCHEMA = {"ts": pl.Datetime("us"), "value": pl.Float64}


def empty_observations() -> pl.DataFrame:
    return pl.DataFrame(schema=OBS_SCHEMA)


class ProviderError(Exception):
    pass


class NotSupported(ProviderError):
    pass


@dataclass
class Provider:
    """Base class for all data sources. Subclasses override the capability methods they support.

    Instances are cheap; the shared httpx client, rate limiter and cache are injected by the registry wrapper.
    """

    id: str = "base"
    name: str = "Base provider"
    capabilities: Capability = Capability.NONE
    rate: RateSpec = field(default_factory=RateSpec)
    license: LicenseSpec = field(default_factory=LicenseSpec)
    egress: Literal["any", "il"] = "any"
    requires: tuple[str, ...] = ()  # settings fields that must be non-empty (api keys)

    def configured(self, settings) -> bool:
        return all(getattr(settings, r, "") for r in self.requires)

    # --- discovery -------------------------------------------------------
    async def search(self, q: str) -> list[SeriesSpec]:
        raise NotSupported(f"{self.id}: search")

    async def describe(self, key: str) -> SeriesSpec:
        """Return a SeriesSpec pre-filled from provider metadata (used by the CATALOG panel)."""
        provider, _, _ = self.id, None, None
        return SeriesSpec(series_id=f"{provider}:{key}", provider=provider, provider_key=key, name=key)

    def parse_url(self, url: str) -> str | None:
        return None

    # --- data ------------------------------------------------------------
    async def get_series(self, spec: SeriesSpec, since: date | None = None, vintage: date | None = None) -> pl.DataFrame:
        raise NotSupported(f"{self.id}: get_series")

    async def get_ohlcv(self, ticker: str, interval: str = "1d", since: date | None = None) -> pl.DataFrame:
        raise NotSupported(f"{self.id}: get_ohlcv")

    async def get_quotes(self, tickers: list[str]) -> list[Quote]:
        raise NotSupported(f"{self.id}: get_quotes")

    async def stream_quotes(self, tickers: list[str]) -> AsyncIterator[Quote]:
        raise NotSupported(f"{self.id}: stream_quotes")
        yield  # pragma: no cover

    async def get_statements(self, ticker: str, kind: str = "all", since: date | None = None) -> list[Statement]:
        raise NotSupported(f"{self.id}: get_statements")

    async def get_news(self, tickers: list[str], since: datetime | None = None) -> list[NewsItem]:
        raise NotSupported(f"{self.id}: get_news")

    async def get_calendar(self, countries: list[str], start: date, end: date) -> list[CalendarEvent]:
        raise NotSupported(f"{self.id}: get_calendar")

    async def get_events(self, tickers: list[str], start: date, end: date) -> list[CorporateEvent]:
        raise NotSupported(f"{self.id}: get_events")

    async def get_holidays(self, country: str, year: int) -> list[Holiday]:
        raise NotSupported(f"{self.id}: get_holidays")

    async def health(self) -> dict[str, Any]:
        return {"ok": True}
