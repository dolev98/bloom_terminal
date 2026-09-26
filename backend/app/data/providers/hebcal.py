"""Hebcal holidays (free, CC BY 4.0 attribution, ~90 req/10s). Maps Jewish holidays to TASE closures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.data.http import get_client
from app.data.providers.base import Capability, Holiday, LicenseSpec, Provider, RateSpec
from app.data.retry import raise_for_retry, retrying

BASE = "https://www.hebcal.com/hebcal"

# TASE is closed on these (major holidays, Israel-observed). Eves (erev) are half-days / closed depending on the year — treated as closed for Pesach/RH/YK/Sukkot eves.
TASE_CLOSED_TITLES = (
    "Purim",
    "Pesach I",
    "Pesach VII",
    "Yom HaAtzma'ut",
    "Shavuot",
    "Tish'a B'Av",
    "Rosh Hashana",
    "Yom Kippur",
    "Sukkot I",
    "Shmini Atzeret",
    "Simchat Torah",
)
TASE_CLOSED_EVES = ("Erev Pesach", "Erev Rosh Hashana", "Erev Yom Kippur", "Erev Sukkot", "Erev Shavuot", "Yom HaZikaron")


@dataclass
class HebcalProvider(Provider):
    id: str = "hebcal"
    name: str = "Hebcal (Jewish/Israeli holidays)"
    capabilities: Capability = Capability.HOLIDAYS
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_second=5, concurrency=2))
    license: LicenseSpec = field(default_factory=lambda: LicenseSpec(grey=False, attribution="Holiday data © Hebcal.com, CC BY 4.0"))

    async def get_holidays(self, country: str, year: int) -> list[Holiday]:
        if country.upper() != "IL":
            return []
        client = get_client()
        params = {"v": "1", "cfg": "json", "year": str(year), "i": "on", "maj": "on", "min": "off", "mod": "on", "nx": "off", "ss": "off", "mf": "off", "c": "off", "geo": "none"}
        async for attempt in retrying():
            with attempt:
                resp = await client.get(BASE, params=params)
                raise_for_retry(resp)
        items = resp.json().get("items", [])
        out: list[Holiday] = []
        for it in items:
            if it.get("category") != "holiday":
                continue
            title = it.get("title", "")
            d = date.fromisoformat(it["date"][:10])
            closed = _is_closed(title)
            if title.startswith("Rosh Hashana") and closed:
                # Two days
                out.append(Holiday(date=d, name=title, country="IL", exchange_closed=True, source="hebcal"))
                continue
            out.append(Holiday(date=d, name=title, country="IL", exchange_closed=closed, source="hebcal"))
        # Sukkot: TASE closed on Sukkot I and Shmini Atzeret/Simchat Torah; chol hamoed is a half day (open) — leave open.
        return sorted(out, key=lambda h: h.date)

    async def health(self) -> dict:
        try:
            hs = await self.get_holidays("IL", date.today().year)
            return {"ok": len(hs) > 5, "count": len(hs)}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}


def _is_closed(title: str) -> bool:
    if any(title.startswith(t) for t in TASE_CLOSED_EVES):
        return True
    if any(title == t or title.startswith(t + " ") for t in TASE_CLOSED_TITLES):
        # exclude e.g. "Pesach II" chol hamoed: only I and VII
        if title.startswith("Pesach") and title not in ("Pesach I", "Pesach VII"):
            return False
        if title.startswith("Sukkot") and title != "Sukkot I":
            return False
        return True
    return False


def next_business_day(d: date, closed: set[date]) -> date:
    while d.weekday() in (5, 6) or d in closed:  # TASE trades Mon-Fri since Jan 2026
        d += timedelta(days=1)
    return d
