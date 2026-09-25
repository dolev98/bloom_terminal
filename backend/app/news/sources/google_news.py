"""Google News RSS search per ticker (grey/unofficial). English query '"Company" OR TICKER when:1d';
Hebrew variant for .TA tickers / tickers with a Hebrew alias. Publisher from <source>; the Google redirect
link is stored as-is (dedup decodes it when the id is the old base64 format)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote

from app.data.providers.base import LicenseSpec, RateSpec
from app.news.entity import load_hebrew_aliases, normalize_name
from app.news.sources.base import (
    NewsSource,
    RawNewsItem,
    base_ticker,
    entry_id,
    entry_time,
    load_sources_yaml,
    parse_rss,
)

DEFAULT_TEMPLATES = {
    "en": "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
    "he": "https://news.google.com/rss/search?q={query}&hl=he&gl=IL&ceid=IL:he",
}


def short_name(name: str) -> str:
    """'Teva Pharmaceutical Industries Limited' -> 'Teva Pharmaceutical Industries' (casing kept)."""
    norm = normalize_name(name)
    if not norm:
        return name
    words = re.findall(r"[\w'.&-]+", name)
    keep = len(norm.split())
    return " ".join(words[:keep]) if words else name


def build_query(ticker: str, name: str | None, window: str = "1d") -> str:
    base = base_ticker(ticker)
    if name and name.upper() != ticker.upper() and name.upper() != base:
        return f'"{short_name(name)}" OR {base} when:{window}'
    return f"{base} when:{window}"


def build_hebrew_query(aliases: list[str], window: str = "1d") -> str:
    return " OR ".join(f'"{a}"' for a in aliases[:3]) + f" when:{window}"


def strip_publisher(title: str, publisher: str | None) -> str:
    if publisher:
        t = re.sub(rf"\s+-\s+{re.escape(publisher)}\s*$", "", title, flags=re.I)
        if t != title:
            return t.strip()
    return re.sub(r"\s+-\s+[^-]{2,40}$", "", title).strip() if title.count(" - ") == 1 else title.strip()


@dataclass
class GoogleNewsSource(NewsSource):
    id: str = "google_news"
    name: str = "Google News RSS"
    kind: str = "rss"
    cadence_s: int = 900
    keep_unmatched: bool = True  # the query ticker is kept as a hint in `tickers` (not trusted)
    trust_provider_tickers: bool = False  # query hint only; the text must mention the company
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_second=1, per_minute=20, concurrency=2))
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(
            grey=True, personal_use_only=True, note="Google News RSS: unofficial"
        )
    )
    templates: dict[str, str] = field(default_factory=dict)
    window: str = "1d"

    def _templates(self) -> dict[str, str]:
        if not self.templates:
            try:
                self.templates = {**DEFAULT_TEMPLATES, **(load_sources_yaml().get("google_news") or {})}
            except Exception:
                self.templates = dict(DEFAULT_TEMPLATES)
        return self.templates

    async def _fetch_query(
        self, lang: str, query: str, ticker: str, since: datetime | None
    ) -> list[RawNewsItem]:
        url = self._templates()[lang].format(query=quote(query, safe=""))
        entries = parse_rss(await self.http_get(url))
        out: list[RawNewsItem] = []
        for e in entries:
            ts = entry_time(e)
            if since and ts < since:
                continue
            src = e.get("source") or {}
            publisher = (src.get("title") if hasattr(src, "get") else None) or None
            title = strip_publisher(e.get("title", ""), publisher)
            if not title:
                continue
            out.append(
                RawNewsItem(
                    source_id=self.id,
                    external_id=entry_id(e),
                    url=e.get("link", ""),
                    title=title,
                    snippet=e.get("summary"),
                    lang=lang,
                    published_at=ts,
                    publisher=publisher,
                    tickers={ticker: 0.6},
                    raw={"query": query, "source_url": src.get("href") if hasattr(src, "get") else None},
                )
            )
        return out

    async def fetch(
        self, tickers: list[str], since: datetime | None, names: dict[str, str] | None = None
    ) -> list[RawNewsItem]:
        names = names or {}
        he = load_hebrew_aliases()
        out: list[RawNewsItem] = []
        for t in tickers:
            t = t.upper()
            try:
                out.extend(await self._fetch_query("en", build_query(t, names.get(t), self.window), t, since))
            except Exception as e:
                self.note_error(f"en:{t}", e)
            aliases = he.get(base_ticker(t))
            if aliases and (t.endswith(".TA") or base_ticker(t) in he):
                try:
                    out.extend(
                        await self._fetch_query("he", build_hebrew_query(aliases, self.window), t, since)
                    )
                except Exception as e:
                    self.note_error(f"he:{t}", e)
        return out
