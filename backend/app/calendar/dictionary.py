"""Indicator dictionary: vendor / official release names -> canonical event keys (loaded from indicator_dictionary.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import yaml

DICT_PATH = Path(__file__).with_name("indicator_dictionary.yaml")

_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}


@dataclass
class Indicator:
    event_key: str
    country: str
    title: str
    category: str = "other"
    importance: int = 1
    kind: str = "macro"
    unit: str | None = None
    linked_series: list[str] = field(default_factory=list)
    headline_series: str | None = None
    headline_transform: str = "level"
    # multiplier from the headline series' stored unit to `unit` (e.g. ICSA is a count, shown in k → 0.001)
    headline_scale: float = 1.0
    release_time: str | None = None  # local time HH:MM in the country's tz (see COUNTRY_TZ)
    period_rule: str = "none"
    release_names: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)


COUNTRY_TZ = {
    "US": "America/New_York",
    "IL": "Asia/Jerusalem",
    "EA": "Europe/Berlin",
    "DE": "Europe/Berlin",
    "GB": "Europe/London",
    "JP": "Asia/Tokyo",
    "CN": "Asia/Shanghai",
    "CA": "America/Toronto",
    "CH": "Europe/Zurich",
    "AU": "Australia/Sydney",
    "NZ": "Pacific/Auckland",
    "GLOBAL": "UTC",
}
COUNTRY_CURRENCY = {
    "US": "USD",
    "IL": "ILS",
    "EA": "EUR",
    "DE": "EUR",
    "GB": "GBP",
    "JP": "JPY",
    "CN": "CNY",
    "CA": "CAD",
    "CH": "CHF",
    "AU": "AUD",
    "NZ": "NZD",
}
FLAGS = {
    "US": "🇺🇸",
    "IL": "🇮🇱",
    "EA": "🇪🇺",
    "DE": "🇩🇪",
    "GB": "🇬🇧",
    "JP": "🇯🇵",
    "CN": "🇨🇳",
    "CA": "🇨🇦",
    "CH": "🇨🇭",
    "AU": "🇦🇺",
    "NZ": "🇳🇿",
    "GLOBAL": "🌐",
}


def normalize(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"\([^)]*\)", " ", s)  # drop "(Aug)", "(Q2)", "(Prel)"
    s = s.replace("m/m", " mom ").replace("y/y", " yoy ").replace("q/q", " qoq ").replace("q/y", " qoy ")
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def slugify(name: str) -> str:
    return normalize(name).replace(" ", "_")[:60] or "event"


class Dictionary:
    def __init__(self, data: dict):
        self.currency_to_country: dict[str, str] = {
            k.upper(): v for k, v in (data.get("currency_to_country") or {}).items()
        }
        self.country_names: dict[str, str] = {}
        for cc, names in (data.get("country_names") or {}).items():
            for n in names:
                self.country_names[normalize(n)] = cc
        self.indicators: list[Indicator] = [
            Indicator(**{k: v for k, v in it.items()}) for it in data.get("indicators", [])
        ]
        self.by_key: dict[str, Indicator] = {i.event_key: i for i in self.indicators}
        self._alias: dict[tuple[str, str], Indicator] = {}
        self._release: dict[tuple[str, str], Indicator] = {}
        for ind in self.indicators:
            for a in [ind.title, *ind.aliases]:
                self._alias.setdefault((ind.country, normalize(a)), ind)
            for r in ind.release_names:
                self._release.setdefault((ind.country, normalize(r)), ind)

    # --- lookups ----------------------------------------------------------
    def country_from_currency(self, cur: str | None) -> str | None:
        return self.currency_to_country.get((cur or "").upper())

    def country_from_name(self, name: str | None) -> str | None:
        if not name:
            return None
        n = normalize(name)
        if n.upper() in COUNTRY_TZ:
            return n.upper()
        return self.country_names.get(n)

    def lookup(self, name: str, country: str) -> Indicator | None:
        """Exact (normalized) alias match within a country; falls back to a prefix match on the alias list."""
        n = normalize(name)
        hit = self._alias.get((country, n))
        if hit:
            return hit
        # tolerate qualifiers such as "Prel"/"Final"/"Adv" appended or prepended to a known alias
        for (cc, alias), ind in self._alias.items():
            if cc != country or len(alias) < 6:
                continue
            if n.startswith(alias + " ") or n.endswith(" " + alias):
                return ind
        return None

    def by_release(self, name: str, country: str = "US") -> Indicator | None:
        n = normalize(name)
        hit = self._release.get((country, n))
        if hit:
            return hit
        for (cc, rel), ind in self._release.items():
            if cc == country and (n.startswith(rel) or rel.startswith(n)):
                return ind
        return None

    def fallback_key(self, name: str, country: str) -> str:
        return f"{country.lower()}.{slugify(name)}"


@lru_cache(maxsize=1)
def load_dictionary(path: Path = DICT_PATH) -> Dictionary:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Dictionary(data)


# --- reference-period helpers ----------------------------------------------


def period_from_text(text: str, year_hint: int | None = None) -> str:
    """'August 2026' -> '2026-08'; 'Aug' + year_hint -> 'YYYY-08'; '2nd Quarter 2026' / 'Q2 2026' / 'Q2' -> '2026Q2'; 'Week ending Sep 20' -> ''."""
    t = text.strip().lower()
    m = re.search(r"(?:q([1-4])|([1-4])(?:st|nd|rd|th)\s+quarter)(?:\s+(\d{4}))?", t)
    if m:
        q = m.group(1) or m.group(2)
        y = m.group(3) or (str(year_hint) if year_hint else "")
        return f"{y}Q{q}" if y else f"Q{q}"
    m = re.search(r"\b([a-z]{3,9})\.?\s*(\d{4})?\b", t)
    if m and m.group(1) in _MONTHS:
        mo = _MONTHS[m.group(1)]
        y = m.group(2) or (str(year_hint) if year_hint else "")
        return f"{y}-{mo:02d}" if y else f"-{mo:02d}"
    m = re.fullmatch(r"(\d{4})-(\d{2})", t)
    if m:
        return t
    return ""


def period_from_rule(rule: str, release: date) -> str:
    if rule == "prev_month":
        first = release.replace(day=1)
        prev = first - timedelta(days=1)
        return f"{prev.year}-{prev.month:02d}"
    if rule == "prev_quarter":
        q = (release.month - 1) // 3  # 0-based current quarter
        if q == 0:
            return f"{release.year - 1}Q4"
        return f"{release.year}Q{q}"
    if rule == "prev_week":
        # BLS claims: week ending the Saturday before the release (release is Thursday)
        sat = release - timedelta(days=(release.weekday() - 5) % 7)
        return sat.isoformat()
    return ""


def fix_period_year(period: str, release: date) -> str:
    """Vendors give 'Aug' without a year: pick the year so that the period precedes the release date."""
    if not period:
        return ""
    if re.fullmatch(r"Q[1-4]", period):
        q = int(period[1])
        y = release.year
        if q > (release.month - 1) // 3 + 1:
            y -= 1
        return f"{y}Q{q}"
    if re.fullmatch(r"-\d{2}", period):
        mo = int(period[1:])
        y = release.year if mo <= release.month else release.year - 1
        return f"{y}-{mo:02d}"
    return period
