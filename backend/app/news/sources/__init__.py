"""News source adapters. `build_sources()` instantiates every adapter; `PROVIDERS` is the list the integrator
can register in app/data/registry.py (the service self-registers missing ones at poll time)."""

from __future__ import annotations

from app.news.sources.alphavantage import AlphaVantageNewsSource
from app.news.sources.base import NewsSource, RawNewsItem, load_sources_yaml
from app.news.sources.finnhub_news import FinnhubNewsSource
from app.news.sources.gdelt import GdeltSource
from app.news.sources.google_news import GoogleNewsSource
from app.news.sources.israel_rss import IsraelRssSource
from app.news.sources.massive import MassiveSource
from app.news.sources.sec_filings import SecFilingsSource
from app.news.sources.wires import WiresSource

__all__ = ["NewsSource", "RawNewsItem", "build_sources", "PROVIDERS", "load_sources_yaml"]


def build_sources() -> list[NewsSource]:
    return [
        SecFilingsSource(),
        WiresSource(),
        FinnhubNewsSource(),
        GoogleNewsSource(),
        MassiveSource(),
        AlphaVantageNewsSource(),
        IsraelRssSource(),
        GdeltSource(),
    ]


PROVIDERS: list[NewsSource] = build_sources()
