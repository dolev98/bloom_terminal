"""Shared base for news sources: the `RawNewsItem` transfer model, a `NewsSource` Provider subclass with
`fetch(tickers, since)`, HTTP helpers (shared client + retry + per-source token bucket) and RSS parsing."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import feedparser
import yaml
from pydantic import BaseModel, Field

from app.data.http import get_client
from app.data.providers.base import Capability, LicenseSpec, Provider, RateSpec
from app.data.ratelimit import LimiterPool
from app.data.retry import raise_for_retry, retrying

log = logging.getLogger(__name__)
SOURCES_YAML = Path(__file__).resolve().parent.parent / "seeds" / "sources.yaml"
_limiters = LimiterPool()


class RawNewsItem(BaseModel):
    """What every adapter returns. `tickers` are provider-supplied {ticker: relevance}; `filing` carries the
    SEC metadata the pipeline upserts into `filings`. Times are naive UTC."""

    source_id: str
    external_id: str
    url: str = ""
    title: str
    snippet: str | None = None
    body: str | None = None
    lang: str = "en"
    published_at: datetime
    publisher: str | None = None
    tickers: dict[str, float] = Field(default_factory=dict)
    sentiment: float | None = None
    filing: dict[str, Any] | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


@dataclass
class NewsSource(Provider):
    """A news adapter. Subclasses set `id`, `kind`, `cadence_s` and implement `fetch`."""

    id: str = "news_base"
    name: str = "News source"
    kind: str = "rss"  # filing | wire | news_api | rss | social
    cadence_s: int = 900
    lang: str = "en"
    keep_unmatched: bool = False  # keep items that link to no known entity
    # False for search-query hints (Google News, GDELT): the query ticker is attached to every result, so the
    # pipeline ignores it and relies on the text linker (a "Apple" search returns apple-picking stories).
    trust_provider_tickers: bool = True
    capabilities: Capability = Capability.NEWS
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_second=2, concurrency=2))
    license: LicenseSpec = field(default_factory=LicenseSpec)
    last_errors: list[str] = field(default_factory=list)

    async def fetch(
        self, tickers: list[str], since: datetime | None, names: dict[str, str] | None = None
    ) -> list[RawNewsItem]:
        raise NotImplementedError

    async def get_news(self, tickers: list[str], since: datetime | None = None):  # Provider protocol
        return await self.fetch(tickers, since)

    # --- helpers ---------------------------------------------------------------------------------------
    async def http_get(self, url: str, params: dict | None = None, headers: dict | None = None) -> str:
        lim = _limiters.get(self.id, self.rate)
        await lim.acquire()
        t0 = time.perf_counter()
        try:
            client = get_client()
            async for attempt in retrying(attempts=3):
                with attempt:
                    resp = await client.get(url, params=params, headers=headers)
                    raise_for_retry(resp)
            return resp.text
        finally:
            lim.release()
            log.debug("%s GET %s %.0fms", self.id, url[:120], (time.perf_counter() - t0) * 1000)

    async def http_json(self, url: str, params: dict | None = None, headers: dict | None = None) -> Any:
        import json

        return json.loads(await self.http_get(url, params, headers))

    def note_error(self, where: str, exc: Exception) -> None:
        msg = f"{where}: {type(exc).__name__}: {exc}"[:300]
        self.last_errors.append(msg)
        log.warning("%s %s", self.id, msg)


# --- RSS helpers ----------------------------------------------------------------------------------------
def parse_rss(text: str) -> list[Any]:
    feed = feedparser.parse(text)
    return list(feed.entries or [])


def entry_time(entry: Any, default: datetime | None = None) -> datetime:
    for k in ("published_parsed", "updated_parsed", "created_parsed"):
        st = entry.get(k) if hasattr(entry, "get") else None
        if st:
            try:
                return datetime(*st[:6])  # feedparser returns UTC struct_time
            except (TypeError, ValueError):
                continue
    return default or utcnow()


def entry_id(entry: Any) -> str:
    return str(entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title") or "")[:300]


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def to_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def load_sources_yaml(path: Path = SOURCES_YAML) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def is_us_symbol(ticker: str) -> bool:
    t = ticker.upper()
    return not (t.startswith("^") or "=" in t or "-USD" in t or "." in t)


def base_ticker(ticker: str) -> str:
    return ticker.upper().split(".", 1)[0]
