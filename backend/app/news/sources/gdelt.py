"""GDELT DOC 2.0: article list per company (ArtList) + tone/volume timeline (TimelineTone) -> derived series.
Free and open; be polite: 1 request / 5 s."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import polars as pl

from app.data.providers.base import LicenseSpec, RateSpec, empty_observations
from app.news.entity import normalize_name
from app.news.sources.base import NewsSource, RawNewsItem, base_ticker

URL = "https://api.gdeltproject.org/api/v2/doc/doc"
LANG = {"english": "en", "hebrew": "he"}


def gdelt_query(ticker: str, name: str | None) -> str:
    """Quoted short company name (GDELT is full-text); falls back to the ticker when no name is known."""
    if name and name.upper() != ticker.upper():
        words = re.findall(r"[\w'.&-]+", name)
        keep = len(normalize_name(name).split()) or len(words)
        short = " ".join(words[:keep])
        return f'"{short}"' if " " in short else short
    return base_ticker(ticker)


def parse_seendate(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None


def parse_timeline(data: dict, series_name: str = "Average Tone") -> pl.DataFrame:
    for series in (data or {}).get("timeline") or []:
        if series.get("series") == series_name or len((data or {}).get("timeline") or []) == 1:
            rows = [(parse_seendate(p.get("date")), p.get("value")) for p in series.get("data") or []]
            rows = [(d, float(v)) for d, v in rows if d is not None and v is not None]
            if not rows:
                break
            return pl.DataFrame({"ts": [r[0] for r in rows], "value": [r[1] for r in rows]}).with_columns(
                pl.col("ts").cast(pl.Datetime("us"))
            )
    return empty_observations()


@dataclass
class GdeltSource(NewsSource):
    id: str = "gdelt"
    name: str = "GDELT DOC 2.0"
    kind: str = "news_api"
    cadence_s: int = 3600
    keep_unmatched: bool = True
    trust_provider_tickers: bool = False  # query hint only; the text must mention the company
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_second=0.2, concurrency=1))
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(grey=False, attribution="Source: GDELT Project")
    )
    maxrecords: int = 250
    timespan: str = "1d"

    async def fetch(
        self, tickers: list[str], since: datetime | None, names: dict[str, str] | None = None
    ) -> list[RawNewsItem]:
        names = names or {}
        out: list[RawNewsItem] = []
        for t in tickers:
            t = t.upper()
            q = gdelt_query(t, names.get(t))
            params = {
                "query": q,
                "mode": "ArtList",
                "format": "json",
                "timespan": self.timespan,
                "maxrecords": self.maxrecords,
                "sort": "DateDesc",
            }
            try:
                data = await self.http_json(URL, params=params)
            except Exception as e:
                self.note_error(t, e)
                continue
            for a in (data or {}).get("articles") or []:
                ts = parse_seendate(a.get("seendate"))
                if ts is None or (since and ts < since) or not a.get("title"):
                    continue
                out.append(
                    RawNewsItem(
                        source_id=self.id,
                        external_id=a.get("url") or a["title"],
                        url=a.get("url") or "",
                        title=a["title"],
                        lang=LANG.get(str(a.get("language", "")).lower(), "en"),
                        published_at=ts,
                        publisher=a.get("domain"),
                        tickers={t: 0.5},
                        raw={"sourcecountry": a.get("sourcecountry"), "query": q},
                    )
                )
        return out

    async def tone(self, ticker: str, name: str | None, timespan: str = "14d") -> pl.DataFrame:
        params = {
            "query": gdelt_query(ticker, name),
            "mode": "TimelineTone",
            "format": "json",
            "timespan": timespan,
        }
        data = await self.http_json(URL, params=params)
        return parse_timeline(data)
