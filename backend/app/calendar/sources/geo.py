"""Geo / political / index-structure events from seeds/geo_events.yaml and the MSCI index-review calendar (ir_dates.csv)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import yaml

from app.calendar.dictionary import COUNTRY_CURRENCY, COUNTRY_TZ
from app.calendar.rules import local_ts
from app.calendar.types import NormalizedEvent
from app.data.http import get_client
from app.data.retry import raise_for_retry, retrying

log = logging.getLogger(__name__)
SEED_PATH = Path(__file__).resolve().parents[1] / "seeds" / "geo_events.yaml"
MSCI_URL = "https://app2.msci.com/eqb/pressreleases/archive/ir_dates.csv"


@lru_cache(maxsize=2)
def load_geo_events(path: Path = SEED_PATH) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = data.get("events") or []
    for r in rows:
        for k in ("date", "start", "end"):
            if isinstance(r.get(k), str):
                r[k] = date.fromisoformat(r[k])
    return rows


def _as_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


@dataclass
class GeoSource:
    id: str = "geo"
    tier: str = "rule"
    path: Path = SEED_PATH

    async def fetch(self, start: date, end: date) -> list[NormalizedEvent]:
        out: list[NormalizedEvent] = []
        for r in load_geo_events(self.path):
            d = _as_date(r.get("date") or r.get("start"))
            if d is None:
                continue
            e = _as_date(r.get("end"))
            if not (start <= d <= end) and not (e and start <= e <= end):
                continue
            cc = r.get("country", "GLOBAL")
            tz = COUNTRY_TZ.get(cc, "UTC")
            verified = bool(r.get("verified", True))
            out.append(
                NormalizedEvent(
                    event_key=r["event_key"],
                    kind=r.get("kind", "geo"),
                    country=cc,
                    currency=COUNTRY_CURRENCY.get(cc),
                    title=r["title"],
                    ts=local_ts(d, r.get("time", "09:00"), tz),
                    end_ts=local_ts(e, "23:59", tz) if e else None,
                    importance=int(r.get("importance", 1)),
                    category=r.get("category", "other"),
                    reference_period=d.isoformat(),
                    source=self.id,
                    source_url=r.get("source_url"),
                    notes=None if verified else "date not yet confirmed by the organiser",
                    tier=self.tier,
                    verified=verified,
                    tags=[] if verified else ["unverified"],
                )
            )
        return out


_DATE_RE = re.compile(
    r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})|(\d{1,2})[-/](\d{1,2})[-/](\d{4})|([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})"
)
_MON = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1
    )
}


def parse_any_date(s: str) -> date | None:
    m = _DATE_RE.search(s or "")
    if not m:
        return None
    try:
        if m.group(1):
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if m.group(4):
            a, b, y = int(m.group(4)), int(m.group(5)), int(m.group(6))
            return date(y, a, b) if a <= 12 else date(y, b, a)
        mon = _MON.get(m.group(7)[:3].lower())
        if mon:
            return date(int(m.group(9)), mon, int(m.group(8)))
    except ValueError:
        return None
    return None


def parse_msci_csv(text: str) -> list[dict]:
    """ir_dates.csv is a press-release wrapper: the data block sits between '#BOD' and '#EOD', pipe-delimited
    (Quarter|Event|Announcement Date|Effective Date, dates MM-DD-YYYY), each line CSV-quoted."""
    out = []
    inside = False
    header: list[str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#BOD"):
            inside = True
            continue
        if line.startswith("#EOD"):
            break
        if not inside or not line:
            continue
        line = line.rstrip(",")
        if line.startswith('"') and line.endswith('"'):
            line = line[1:-1]
        line = line.replace('""', '"').replace('"', "")
        cells = [c.strip() for c in line.split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        rec = dict(zip(header, cells, strict=False))
        ann = eff = None
        name = ""
        for k, v in rec.items():
            if "announc" in k:
                ann = parse_any_date(v)
            elif "effective" in k or "implement" in k:
                eff = parse_any_date(v)
            elif "event" in k or "quarter" in k:
                name = f"{name} {v}".strip()
        if ann or eff:
            out.append({"name": name or "MSCI index review", "announcement": ann, "effective": eff})
    return out


@dataclass
class MsciSource:
    id: str = "msci"
    tier: str = "official"

    async def fetch(self, start: date, end: date) -> list[NormalizedEvent]:
        client = get_client()
        try:
            async for attempt in retrying(attempts=2):
                with attempt:
                    resp = await client.get(MSCI_URL)
                    raise_for_retry(resp)
        except Exception as e:
            log.info("msci ir_dates unavailable: %s", e)
            return []
        out: list[NormalizedEvent] = []
        for rec in parse_msci_csv(resp.text):
            for phase, d, title in (
                ("announcement", rec["announcement"], "MSCI index review announcement"),
                ("effective", rec["effective"], "MSCI index review effective (close)"),
            ):
                if d is None or not (start <= d <= end):
                    continue
                out.append(
                    NormalizedEvent(
                        event_key=f"global.msci_{phase}",
                        kind="index_rebalance",
                        country="GLOBAL",
                        title=f"{title}: {rec['name']}",
                        ts=local_ts(
                            d,
                            "22:30" if phase == "announcement" else "16:00",
                            "Europe/London" if phase == "announcement" else "America/New_York",
                        ),
                        importance=2,
                        category="other",
                        reference_period=d.isoformat(),
                        source=self.id,
                        source_url=MSCI_URL,
                        tier=self.tier,
                    )
                )
        return out
