"""Israeli financial press RSS (TheMarker srv feeds; Globes/Calcalist/Bizportal need an Israeli IP).
Hebrew items are entity-linked with the Hebrew alias seed; unmatched items are kept (general IL market news)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.data.providers.base import LicenseSpec, RateSpec
from app.news.dedup import strip_html
from app.news.sources.base import NewsSource, RawNewsItem, entry_id, entry_time, load_sources_yaml, parse_rss


@dataclass
class IsraelRssSource(NewsSource):
    id: str = "israel_rss"
    name: str = "Israeli press RSS"
    kind: str = "rss"
    cadence_s: int = 900
    lang: str = "he"
    keep_unmatched: bool = True
    egress: Literal["any", "il"] = "il"
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_second=1, per_minute=20, concurrency=2))
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(grey=False, note="public RSS; IL IP for some")
    )
    feeds: list[dict] = field(default_factory=list)

    def _feeds(self) -> list[dict]:
        if not self.feeds:
            self.feeds = [f for f in (load_sources_yaml().get("israel") or []) if f.get("enabled", True)]
        return self.feeds

    async def fetch(
        self, tickers: list[str], since: datetime | None, names: dict[str, str] | None = None
    ) -> list[RawNewsItem]:
        out: list[RawNewsItem] = []
        for feed in self._feeds():
            try:
                entries = parse_rss(await self.http_get(feed["url"]))
            except Exception as e:
                self.note_error(feed.get("id", feed["url"]), e)
                continue
            for e in entries:
                ts = entry_time(e)
                if since and ts < since:
                    continue
                title = (e.get("title") or "").strip()
                if not title:
                    continue
                out.append(
                    RawNewsItem(
                        source_id=self.id,
                        external_id=f"{feed.get('id', 'il')}:{entry_id(e)}",
                        url=e.get("link", ""),
                        title=title,
                        snippet=strip_html(e.get("summary")) or None,
                        lang=feed.get("lang", "he"),
                        published_at=ts,
                        publisher=feed.get("publisher"),
                        raw={"feed": feed.get("id"), "author": e.get("author")},
                    )
                )
        return out
