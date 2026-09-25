"""Official US schedules: BLS news-release ICS feed and the BEA release_dates.json."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from icalendar import Calendar

from app.calendar.dictionary import load_dictionary, period_from_rule
from app.calendar.rules import local_ts
from app.calendar.types import NormalizedEvent, to_utc_naive
from app.data.http import get_client
from app.data.retry import raise_for_retry, retrying

log = logging.getLogger(__name__)
BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
BEA_URL = "https://apps.bea.gov/API/signup/release_dates.json"
NY = ZoneInfo("America/New_York")
_TZ_ALIAS = {"US-Eastern": NY, "Eastern": NY, "America/New_York": NY, "EST": NY, "EDT": NY}


def _bls_user_agent() -> str:
    """bls.gov's edge only lets automated clients through when the UA carries a contact (+mailto:)."""
    from app.core.config import get_settings

    ua = (get_settings().sec_user_agent or "").strip()
    email = next((tok for tok in ua.replace("(", " ").replace(")", " ").split() if "@" in tok), "")
    return f"terminal/0.1 (personal research terminal; +mailto:{email or 'research@example.com'})"


def parse_bls_ics(text: str) -> list[tuple[str, datetime]]:
    """-> [(summary, naive UTC datetime)]. DTSTART uses a custom TZID 'US-Eastern'; naive stamps are treated as ET."""
    cal = Calendar.from_ical(text)
    out: list[tuple[str, datetime]] = []
    for comp in cal.walk("VEVENT"):
        summary = str(comp.get("SUMMARY", "")).strip()
        dt = comp.get("DTSTART")
        if not summary or dt is None:
            continue
        val = dt.dt
        if isinstance(val, datetime):
            if val.tzinfo is None:
                tzid = str(dt.params.get("TZID", "US-Eastern"))
                val = val.replace(tzinfo=_TZ_ALIAS.get(tzid, NY))
            out.append((summary, to_utc_naive(val)))
        else:  # all-day: assume 08:30 ET
            out.append((summary, local_ts(val, "08:30", "America/New_York")))
    return out


@dataclass
class BlsIcsSource:
    id: str = "bls_ics"
    tier: str = "official"

    async def fetch(self, start: date, end: date) -> list[NormalizedEvent]:
        client = get_client()
        try:
            async for attempt in retrying(attempts=2):
                with attempt:
                    resp = await client.get(
                        BLS_ICS_URL, headers={"User-Agent": _bls_user_agent(), "Accept": "text/calendar,*/*"}
                    )
                    raise_for_retry(resp)
        except Exception as e:
            log.info("bls ics unavailable: %s", e)
            return []
        return self.parse(resp.text, start, end)

    def parse(self, text: str, start: date, end: date) -> list[NormalizedEvent]:
        dic = load_dictionary()
        out: list[NormalizedEvent] = []
        for summary, ts in parse_bls_ics(text):
            if not (start <= ts.date() <= end):
                continue
            ind = dic.by_release(summary, "US")
            if ind is None:
                continue
            out.append(
                NormalizedEvent(
                    event_key=ind.event_key,
                    kind=ind.kind,
                    country="US",
                    currency="USD",
                    title=ind.title,
                    ts=ts,
                    importance=ind.importance,
                    category=ind.category,
                    unit=ind.unit,
                    reference_period=period_from_rule(ind.period_rule, ts.date()),
                    source=self.id,
                    source_url="https://www.bls.gov/schedule/",
                    linked_series=list(ind.linked_series),
                    tier=self.tier,
                    raw={"summary": summary},
                )
            )
        return out


def parse_bea_json(data: dict) -> list[tuple[str, datetime]]:
    """{release_name: {release_dates: [ISO-8601 UTC strings]}} -> [(name, naive UTC datetime)] (deduped)."""
    out: list[tuple[str, datetime]] = []
    seen: set[tuple[str, datetime]] = set()
    for name, body in (data or {}).items():
        dates = (body or {}).get("release_dates") if isinstance(body, dict) else body
        for s in dates or []:
            try:
                dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            key = (name, to_utc_naive(dt))
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


@dataclass
class BeaSource:
    id: str = "bea"
    tier: str = "official"

    async def fetch(self, start: date, end: date) -> list[NormalizedEvent]:
        client = get_client()
        try:
            async for attempt in retrying(attempts=2):
                with attempt:
                    resp = await client.get(BEA_URL)
                    raise_for_retry(resp)
            data = resp.json()
        except Exception as e:
            log.info("bea release_dates unavailable: %s", e)
            return []
        return self.parse(data, start, end)

    def parse(self, data: dict, start: date, end: date) -> list[NormalizedEvent]:
        dic = load_dictionary()
        out: list[NormalizedEvent] = []
        for name, ts in parse_bea_json(data):
            if not (start <= ts.date() <= end):
                continue
            ind = dic.by_release(name, "US")
            if ind is None:
                continue
            out.append(
                NormalizedEvent(
                    event_key=ind.event_key,
                    kind=ind.kind,
                    country="US",
                    currency="USD",
                    title=ind.title,
                    ts=ts,
                    importance=ind.importance,
                    category=ind.category,
                    unit=ind.unit,
                    reference_period=period_from_rule(ind.period_rule, ts.date()),
                    source=self.id,
                    source_url="https://www.bea.gov/news/schedule",
                    linked_series=list(ind.linked_series),
                    tier=self.tier,
                    raw={"release_name": name},
                )
            )
        return out
