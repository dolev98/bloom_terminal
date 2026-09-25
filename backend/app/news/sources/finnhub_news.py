"""Finnhub company news (licensed free tier; US symbols only) via the registry's FinnhubProvider."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.data.providers.base import LicenseSpec, RateSpec
from app.news.sources.base import NewsSource, RawNewsItem, is_us_symbol, to_naive_utc, utcnow


@dataclass
class FinnhubNewsSource(NewsSource):
    id: str = "finnhub_news"
    name: str = "Finnhub company news"
    kind: str = "news_api"
    cadence_s: int = 900
    keep_unmatched: bool = True
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_minute=50, concurrency=2))
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(grey=False, note="Finnhub free: personal use")
    )
    requires: tuple[str, ...] = ("finnhub_api_key",)

    async def fetch(
        self, tickers: list[str], since: datetime | None, names: dict[str, str] | None = None
    ) -> list[RawNewsItem]:
        from app.data.registry import get_registry

        reg = get_registry()
        fh = reg.get("finnhub")
        since = since or utcnow() - timedelta(days=3)
        out: list[RawNewsItem] = []
        for t in tickers:
            t = t.upper()
            if not is_us_symbol(t):
                continue
            try:
                items = await reg.call(fh, "get_news", lambda t=t: fh.get_news([t], since), key=t)
            except Exception as e:
                self.note_error(t, e)
                continue
            for it in items:
                ts = to_naive_utc(it.ts) or utcnow()
                if since and ts < since:
                    continue
                out.append(
                    RawNewsItem(
                        source_id=self.id,
                        external_id=it.id,
                        url=it.url,
                        title=it.title,
                        snippet=it.summary,
                        published_at=ts,
                        publisher=it.publisher,
                        tickers={t: 0.9},
                        raw=dict(it.raw),
                    )
                )
        return out
