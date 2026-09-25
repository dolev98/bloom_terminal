"""Massive (ex-Polygon) reference news: per-ticker articles with `insights[].sentiment`.
Basic (free) tier is 5 calls/min — tickers are rotated across polls (`max_tickers_per_poll`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core.config import get_settings
from app.data.providers.base import LicenseSpec, RateSpec
from app.news.sources.base import NewsSource, RawNewsItem, is_us_symbol, to_naive_utc, utcnow

URL = "https://api.massive.com/v2/reference/news"
SENTIMENT = {"positive": 0.6, "negative": -0.6, "neutral": 0.0}


def parse_article(a: dict, source_id: str) -> RawNewsItem | None:
    ts = a.get("published_utc")
    try:
        dt = to_naive_utc(datetime.fromisoformat(str(ts).replace("Z", "+00:00"))) if ts else None
    except ValueError:
        dt = None
    if dt is None or not a.get("title"):
        return None
    tickers = {str(t).upper(): 0.8 for t in a.get("tickers") or []}
    per_ticker: dict[str, float] = {}
    for ins in a.get("insights") or []:
        t = str(ins.get("ticker") or "").upper()
        if t:
            per_ticker[t] = SENTIMENT.get(str(ins.get("sentiment")).lower(), 0.0)
            tickers[t] = 0.9
    overall = sum(per_ticker.values()) / len(per_ticker) if per_ticker else None
    pub = a.get("publisher") or {}
    return RawNewsItem(
        source_id=source_id,
        external_id=str(a.get("id") or a.get("article_url")),
        url=a.get("article_url") or "",
        title=a["title"],
        snippet=a.get("description"),
        published_at=dt,
        publisher=pub.get("name") if isinstance(pub, dict) else None,
        tickers=tickers,
        sentiment=overall,
        raw={"keywords": a.get("keywords"), "insights": per_ticker, "author": a.get("author")},
    )


@dataclass
class MassiveSource(NewsSource):
    id: str = "massive"
    name: str = "Massive news"
    kind: str = "news_api"
    cadence_s: int = 1800
    keep_unmatched: bool = True
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_minute=5, concurrency=1))
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(grey=False, note="Massive Basic: 5 calls/min")
    )
    requires: tuple[str, ...] = ("massive_api_key",)
    max_tickers_per_poll: int = 10
    _offset: int = 0

    async def fetch(
        self, tickers: list[str], since: datetime | None, names: dict[str, str] | None = None
    ) -> list[RawNewsItem]:
        key = getattr(get_settings(), "massive_api_key", "") or ""
        if not key:
            return []
        us = [t.upper() for t in tickers if is_us_symbol(t)]
        if not us:
            return []
        start = self._offset % len(us)
        batch = (us + us)[start : start + min(self.max_tickers_per_poll, len(us))]
        self._offset = (start + len(batch)) % len(us)
        since = since or utcnow() - timedelta(days=2)
        out: list[RawNewsItem] = []
        for t in batch:
            params = {
                "ticker": t,
                "published_utc.gte": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": 1000,
                "order": "desc",
                "apiKey": key,
            }
            try:
                data = await self.http_json(URL, params=params)
            except Exception as e:
                self.note_error(t, e)
                continue
            for a in (data or {}).get("results") or []:
                item = parse_article(a, self.id)
                if item:
                    item.tickers.setdefault(t, 0.8)
                    out.append(item)
        return out
