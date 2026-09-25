"""Press-release wires via public RSS: GlobeNewswire subject feeds (earnings, M&A), PR Newswire, Business Wire.
Feeds are broad (every issuer) — items are entity-matched locally and unmatched ones dropped."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from app.data.providers.base import LicenseSpec, RateSpec
from app.news.sources.base import NewsSource, RawNewsItem, entry_id, entry_time, load_sources_yaml, parse_rss

_EXCH_TICKER_RE = re.compile(
    r"^(?:NASDAQ|NYSE|TASE|AMEX|OTC[A-Z]*|TSX|LSE|NYSE\s*American)\s*:\s*([A-Za-z.\-]{1,8})$", re.I
)


def tickers_from_categories(entry) -> dict[str, float]:
    out: dict[str, float] = {}
    for tag in entry.get("tags") or []:
        term = (tag.get("term") or "").strip()
        m = _EXCH_TICKER_RE.match(term)
        if m:
            out[m.group(1).upper()] = 0.95
    return out


@dataclass
class WiresSource(NewsSource):
    id: str = "wires"
    name: str = "Press-release wires"
    kind: str = "wire"
    cadence_s: int = 600
    keep_unmatched: bool = False
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_second=1, per_minute=30, concurrency=2))
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(grey=False, note="public syndication RSS")
    )
    feeds: list[dict] = field(default_factory=list)

    def _feeds(self) -> list[dict]:
        if not self.feeds:
            self.feeds = [f for f in (load_sources_yaml().get("wires") or []) if f.get("enabled", True)]
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
                        external_id=f"{feed.get('id', 'wire')}:{entry_id(e)}",
                        url=e.get("link", ""),
                        title=title,
                        snippet=e.get("summary"),
                        published_at=ts,
                        publisher=feed.get("publisher") or "wire",
                        tickers=tickers_from_categories(e),
                        raw={
                            "feed": feed.get("id"),
                            "subject": feed.get("subject") or e.get("dc_subject"),
                            "keyword": e.get("dc_keyword"),
                            "industry": e.get("prn_industry"),
                        },
                    )
                )
        return out
