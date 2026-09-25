"""Alpha Vantage NEWS_SENTIMENT (25 requests/day). NOTE: a comma list in `tickers=` is an AND filter
(articles mentioning *all* tickers), so we query one ticker per call and rotate, `calls_per_poll` per poll
(default 2 -> 24 calls/day at the 2 h cadence)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.core.config import get_settings
from app.data.providers.base import LicenseSpec, RateSpec
from app.news.sources.base import NewsSource, RawNewsItem, is_us_symbol

URL = "https://www.alphavantage.co/query"


def parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None


def parse_feed_item(a: dict, source_id: str) -> RawNewsItem | None:
    dt = parse_time(a.get("time_published"))
    if dt is None or not a.get("title"):
        return None
    tickers: dict[str, float] = {}
    per: dict[str, float] = {}
    for ts in a.get("ticker_sentiment") or []:
        t = str(ts.get("ticker") or "").upper()
        if not t or ":" in t:
            continue
        try:
            rel = float(ts.get("relevance_score") or 0)
            sc = float(ts.get("ticker_sentiment_score") or 0)
        except ValueError:
            rel, sc = 0.0, 0.0
        if rel >= 0.15:
            tickers[t] = round(rel, 3)
            per[t] = sc
    try:
        overall = (
            float(a.get("overall_sentiment_score")) if a.get("overall_sentiment_score") is not None else None
        )
    except (TypeError, ValueError):
        overall = None
    return RawNewsItem(
        source_id=source_id,
        external_id=a.get("url") or a["title"],
        url=a.get("url") or "",
        title=a["title"],
        snippet=a.get("summary"),
        published_at=dt,
        publisher=a.get("source"),
        tickers=tickers,
        sentiment=overall,
        raw={
            "topics": [t.get("topic") for t in a.get("topics") or []],
            "ticker_sentiment": per,
            "authors": a.get("authors"),
        },
    )


@dataclass
class AlphaVantageNewsSource(NewsSource):
    id: str = "alphavantage"
    name: str = "Alpha Vantage NEWS_SENTIMENT"
    kind: str = "news_api"
    cadence_s: int = 7200
    keep_unmatched: bool = True
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_minute=5, per_day=25, concurrency=1))
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(grey=False, note="Alpha Vantage free: 25/day")
    )
    requires: tuple[str, ...] = ("alphavantage_api_key",)
    calls_per_poll: int = 2
    _offset: int = 0

    async def fetch(
        self, tickers: list[str], since: datetime | None, names: dict[str, str] | None = None
    ) -> list[RawNewsItem]:
        key = getattr(get_settings(), "alphavantage_api_key", "") or ""
        if not key:
            return []
        us = [t.upper() for t in tickers if is_us_symbol(t)]
        if not us:
            return []
        start = self._offset % len(us)
        batch = (us + us)[start : start + min(self.calls_per_poll, len(us))]
        self._offset = (start + len(batch)) % len(us)
        out: list[RawNewsItem] = []
        for t in batch:
            params = {
                "function": "NEWS_SENTIMENT",
                "tickers": t,
                "limit": 1000,
                "sort": "LATEST",
                "apikey": key,
            }
            if since:
                params["time_from"] = since.strftime("%Y%m%dT%H%M")
            try:
                data = await self.http_json(URL, params=params)
            except Exception as e:
                self.note_error(t, e)
                continue
            if not isinstance(data, dict) or "feed" not in data:
                self.note_error(t, RuntimeError(str(data)[:160] if data else "empty response"))
                continue
            for a in data.get("feed") or []:
                item = parse_feed_item(a, self.id)
                if item:
                    item.tickers.setdefault(t, 0.5)
                    out.append(item)
        return out
