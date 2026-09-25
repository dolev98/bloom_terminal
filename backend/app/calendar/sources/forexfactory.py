"""ForexFactory weekly calendar JSON (forecast/previous only; current week; ~2 requests / 5 min -> cached 60 min)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime

from app.calendar.dictionary import load_dictionary
from app.calendar.types import NormalizedEvent
from app.data.cache import make_key
from app.data.http import get_client
from app.data.retry import raise_for_retry, retrying

log = logging.getLogger(__name__)
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CACHE_TTL = 60 * 60
_IMPACT = {"high": 3, "medium": 2, "low": 1, "holiday": 1}
_NUM_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*([kmbt%]?)", re.I)


def parse_number(s: str | None) -> float | None:
    """'0.3%' -> 0.3; '15.2B' -> 15.2; '-2.0%' -> -2.0; '' -> None (units are kept in the dictionary/unit field)."""
    if s is None:
        return None
    m = _NUM_RE.match(str(s).strip().replace(",", ""))
    return float(m.group(1)) if m else None


_UNITS = {"%": "%", "k": "k", "m": "M", "b": "bn", "t": "tn"}
_CHANGE_TITLE_RE = re.compile(r"\b(m/m|y/y|q/q|mom|yoy|qoq)\b", re.I)


def parse_unit(*values: str | None) -> str | None:
    """Unit suffix carried by FF values: '0.3%' -> '%', '201K' -> 'k', '-0.7M' -> 'M', '15.2B' -> 'bn'."""
    for s in values:
        m = _NUM_RE.match(str(s or "").strip().replace(",", ""))
        if m and m.group(2):
            return _UNITS[m.group(2).lower()]
    return None


def ff_unit(
    title: str, forecast: str | None, previous: str | None, dictionary_unit: str | None = None
) -> str | None:
    """Dictionary unit first, then the suffix on the values; "m/m", "y/y", "q/q" titles are percentage changes."""
    if dictionary_unit:
        return dictionary_unit
    unit = parse_unit(forecast, previous)
    if unit:
        return unit
    return "%" if _CHANGE_TITLE_RE.search(title) else None


class ForexFactoryDenied(Exception):
    pass


def parse_ff(text: str) -> list[dict]:
    t = text.lstrip()
    if not t.startswith("["):
        raise ForexFactoryDenied(f"non-JSON response: {t[:80]!r}")
    data = json.loads(t)
    return data if isinstance(data, list) else []


@dataclass
class ForexFactorySource:
    id: str = "forexfactory"
    tier: str = "forexfactory"

    async def _load(self) -> list[dict]:
        from app.state import get_cache

        cache = get_cache()
        key = make_key("forexfactory", "thisweek", {})
        hit = await cache.get(key)
        if hit is not None:
            return hit
        client = get_client()
        async for attempt in retrying(attempts=2):
            with attempt:
                resp = await client.get(FF_URL, headers={"Accept": "application/json"})
                raise_for_retry(resp)
        rows = parse_ff(resp.text)
        await cache.put(key, rows, CACHE_TTL, provider="forexfactory", op="thisweek")
        return rows

    async def fetch(self, start: date, end: date) -> list[NormalizedEvent]:
        try:
            rows = await self._load()
        except ForexFactoryDenied as e:
            log.warning("forexfactory: %s", e)
            return []
        except Exception as e:
            log.info("forexfactory unavailable: %s", e)
            return []
        return self.parse(rows, start, end)

    def parse(self, rows: list[dict], start: date, end: date) -> list[NormalizedEvent]:
        dic = load_dictionary()
        out: list[NormalizedEvent] = []
        for r in rows:
            try:
                ts = datetime.fromisoformat(str(r.get("date")))
            except ValueError:
                continue
            title = str(r.get("title") or "").strip()
            cc = dic.country_from_currency(r.get("country"))
            if not title or not cc or not (start <= ts.date() <= end):
                continue
            impact = str(r.get("impact") or "Low").lower()
            ind = dic.lookup(title, cc)
            is_holiday = impact == "holiday"
            out.append(
                NormalizedEvent(
                    event_key=ind.event_key
                    if ind
                    else (
                        f"{cc.lower()}.holiday.{ts.date().isoformat()}"
                        if is_holiday
                        else dic.fallback_key(title, cc)
                    ),
                    kind=ind.kind if ind else ("holiday" if is_holiday else "macro"),
                    country=cc,
                    currency=str(r.get("country") or "").upper() or None,
                    title=ind.title if ind else title,
                    ts=ts,
                    importance=ind.importance if ind else _IMPACT.get(impact, 1),
                    category=ind.category if ind else "other",
                    unit=ff_unit(title, r.get("forecast"), r.get("previous"), ind.unit if ind else None),
                    reference_period="",
                    consensus=parse_number(r.get("forecast")),
                    consensus_source="forexfactory" if parse_number(r.get("forecast")) is not None else None,
                    previous=parse_number(r.get("previous")),
                    source=self.id,
                    source_url="https://www.forexfactory.com/calendar",
                    linked_series=list(ind.linked_series) if ind else [],
                    tier=self.tier,
                    raw={
                        "title": title,
                        "impact": r.get("impact"),
                        "forecast": r.get("forecast"),
                        "previous": r.get("previous"),
                    },
                )
            )
        return out
