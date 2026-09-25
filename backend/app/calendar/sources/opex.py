"""US options expiries (3rd Friday / quad witching), NYSE holidays (seed YAML) and other countries' public holidays (Nager.Date)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

from app.calendar.rules import local_ts, opex_dates
from app.calendar.types import NormalizedEvent
from app.data.http import get_client
from app.data.retry import raise_for_retry, retrying

log = logging.getLogger(__name__)
SEED_PATH = Path(__file__).resolve().parents[1] / "seeds" / "exchange_holidays.yaml"
NAGER_URLS = (
    "https://date.nager.at/api/v3/PublicHolidays/{year}/{cc}",
    "https://nagerholidays.com/api/v4/Holidays/{cc}/{year}",
)
NAGER_COUNTRIES = ("GB", "DE", "JP", "CA", "CH", "AU", "CN")


@lru_cache(maxsize=2)
def load_exchange_holidays(path: Path = SEED_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def nyse_holidays(years: list[int], path: Path = SEED_PATH) -> dict[date, str]:
    data = load_exchange_holidays(path).get("nyse", {})
    out: dict[date, str] = {}
    for y in years:
        for row in (data.get("closed") or {}).get(y) or []:
            d = row["date"] if isinstance(row["date"], date) else date.fromisoformat(str(row["date"]))
            out[d] = row.get("name", "NYSE closed")
    return out


def nyse_early_closes(years: list[int], path: Path = SEED_PATH) -> dict[date, str]:
    data = load_exchange_holidays(path).get("nyse", {})
    out: dict[date, str] = {}
    for y in years:
        for row in (data.get("early_close") or {}).get(y) or []:
            d = row["date"] if isinstance(row["date"], date) else date.fromisoformat(str(row["date"]))
            out[d] = row.get("name", "early close")
    return out


@dataclass
class OpexSource:
    id: str = "opex"
    tier: str = "rule"
    path: Path = SEED_PATH

    async def fetch(self, start: date, end: date) -> list[NormalizedEvent]:
        years = list(range(start.year, end.year + 1))
        hol = nyse_holidays(years, self.path)
        src = load_exchange_holidays(self.path).get("nyse", {}).get("source_url")
        out: list[NormalizedEvent] = []
        for y in years:
            for d, quad in opex_dates(y, set(hol)):
                if not (start <= d <= end):
                    continue
                out.append(
                    NormalizedEvent(
                        event_key="us.quad_witching" if quad else "us.opex",
                        kind="expiry",
                        country="US",
                        currency="USD",
                        title="Quad witching (index futures/options + stock options expiry)"
                        if quad
                        else "Monthly options expiration (OpEx)",
                        ts=local_ts(d, "16:00", "America/New_York"),
                        importance=2 if quad else 1,
                        category="other",
                        reference_period=f"{d.year}-{d.month:02d}",
                        source=self.id,
                        source_url="https://www.cboe.com/about/hours/us-options/",
                        notes="rule: 3rd Friday (Thursday when the Friday is an exchange holiday)",
                        tier=self.tier,
                        tags=["quad"] if quad else [],
                    )
                )
                if quad:
                    out.append(
                        NormalizedEvent(
                            event_key="us.sp_rebalance",
                            kind="index_rebalance",
                            country="US",
                            currency="USD",
                            title="S&P Dow Jones quarterly index rebalance (effective after close)",
                            ts=local_ts(d, "16:00", "America/New_York"),
                            importance=2,
                            category="other",
                            reference_period=f"{d.year}-{d.month:02d}",
                            source=self.id,
                            source_url="https://www.spglobal.com/spdji/en/",
                            notes="rule: 3rd Friday of Mar/Jun/Sep/Dec",
                            tier=self.tier,
                        )
                    )
        for d, name in hol.items():
            if start <= d <= end:
                out.append(_holiday("US", d, f"NYSE closed: {name}", src, self.id, "USD"))
        for d, name in nyse_early_closes(years, self.path).items():
            if start <= d <= end:
                out.append(
                    _holiday(
                        "US",
                        d,
                        f"NYSE early close: {name}",
                        src,
                        self.id,
                        "USD",
                        key=f"us.early_close.{d.isoformat()}",
                    )
                )
        return out


def _holiday(
    cc: str, d: date, title: str, url: str | None, source: str, cur: str | None, key: str | None = None
) -> NormalizedEvent:
    return NormalizedEvent(
        event_key=key or f"{cc.lower()}.holiday.{d.isoformat()}",
        kind="holiday",
        country=cc,
        currency=cur,
        title=title,
        ts=local_ts(d, "00:00", "UTC"),
        importance=1,
        category="other",
        reference_period=d.isoformat(),
        source=source,
        source_url=url,
        tier="rule",
    )


@dataclass
class NagerHolidaysSource:
    id: str = "nager"
    tier: str = "rule"
    countries: tuple[str, ...] = NAGER_COUNTRIES

    async def fetch_country_year(self, cc: str, year: int) -> list[dict]:
        client = get_client()
        last_err: Exception | None = None
        for tmpl in NAGER_URLS:
            url = tmpl.format(cc=cc, year=year)
            try:
                async for attempt in retrying(attempts=2):
                    with attempt:
                        resp = await client.get(url)
                        raise_for_retry(resp)
                data = resp.json()
                if isinstance(data, list):
                    return data
            except Exception as e:  # try the next host
                last_err = e
        if last_err:
            log.info("nager %s/%s unavailable: %s", cc, year, last_err)
        return []

    async def fetch(self, start: date, end: date) -> list[NormalizedEvent]:
        out: list[NormalizedEvent] = []
        for cc in self.countries:
            for y in range(start.year, end.year + 1):
                for h in await self.fetch_country_year(cc, y):
                    try:
                        d = date.fromisoformat(str(h.get("date"))[:10])
                    except ValueError:
                        continue
                    if not (start <= d <= end):
                        continue
                    national = h.get("global") if "global" in h else h.get("nationalHoliday", True)
                    if national is False:
                        continue
                    types = h.get("types") or h.get("holidayTypes") or []
                    if types and "Public" not in types and "Bank" not in types:
                        continue
                    out.append(
                        _holiday(
                            cc,
                            d,
                            f"{h.get('name') or h.get('localName')} (public holiday)",
                            "https://date.nager.at/",
                            self.id,
                            None,
                        )
                    )
        return out
