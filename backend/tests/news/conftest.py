from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.news.sources.base import NewsSource, RawNewsItem

FIX = Path(__file__).with_name("fixtures")


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class FakeSource(NewsSource):
    """Offline adapter returning canned items (id/kind configurable)."""

    def __init__(
        self,
        sid: str = "google_news",
        kind: str = "rss",
        items: list[RawNewsItem] | None = None,
        keep_unmatched: bool = True,
    ):
        super().__init__(id=sid, name=sid, kind=kind, keep_unmatched=keep_unmatched)
        self.items = items or []

    async def fetch(self, tickers, since, names=None):
        return list(self.items)


def mk(
    title: str,
    url: str,
    sid: str = "google_news",
    publisher: str | None = "Reuters",
    ts: datetime | None = None,
    tickers: dict | None = None,
    snippet: str | None = None,
    external_id: str | None = None,
    lang: str = "en",
    filing: dict | None = None,
) -> RawNewsItem:
    return RawNewsItem(
        source_id=sid,
        external_id=external_id or url,
        url=url,
        title=title,
        snippet=snippet,
        lang=lang,
        published_at=ts or datetime(2026, 9, 23, 12, 0),
        publisher=publisher,
        tickers=tickers or {},
        filing=filing,
    )


@pytest.fixture
def now():
    return datetime(2026, 9, 23, 12, 0)


@pytest.fixture
def old(now):
    return now - timedelta(days=5)
