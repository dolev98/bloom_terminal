"""Central-bank decision calendar from seeds/central_banks.yaml (+ FOMC minutes / Beige Book rules)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

from app.calendar.dictionary import COUNTRY_CURRENCY, load_dictionary
from app.calendar.rules import beige_book_date, fomc_minutes_date, local_ts
from app.calendar.types import NormalizedEvent

SEED_PATH = Path(__file__).resolve().parents[1] / "seeds" / "central_banks.yaml"


@lru_cache(maxsize=4)
def load_central_banks(path: Path = SEED_PATH) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    banks = data.get("banks") or []
    for b in banks:
        for d in b.get("decisions") or []:
            if isinstance(d.get("date"), str):
                d["date"] = date.fromisoformat(d["date"])
            if "start" in d and isinstance(d["start"], str):
                d["start"] = date.fromisoformat(d["start"])
    return banks


@dataclass
class CentralBankSource:
    id: str = "central_banks"
    tier: str = "official"
    path: Path = SEED_PATH

    async def fetch(self, start: date, end: date) -> list[NormalizedEvent]:
        out: list[NormalizedEvent] = []
        dic = load_dictionary()
        for bank in load_central_banks(self.path):
            cc = bank["country"]
            ind = dic.by_key.get(bank["event_key"])
            tz = bank.get("tz", "UTC")
            for d in bank.get("decisions") or []:
                dd: date = d["date"]
                verified = bool(d.get("verified", True))
                tags = [] if verified else ["unverified"]
                note_bits = []
                if d.get("sep"):
                    note_bits.append("SEP / dot plot")
                if d.get("mpr"):
                    note_bits.append("Monetary Policy Report")
                if not verified:
                    note_bits.append("date not yet confirmed by the bank")
                if start <= dd <= end:
                    out.append(
                        NormalizedEvent(
                            event_key=bank["event_key"],
                            kind="cb_decision",
                            country=cc,
                            currency=COUNTRY_CURRENCY.get(cc),
                            title=bank.get("title") or (ind.title if ind else f"{bank['name']} decision"),
                            ts=local_ts(dd, bank.get("decision_time", "12:00"), tz),
                            end_ts=None,
                            importance=3,
                            category="rates",
                            unit="%",
                            reference_period=dd.isoformat(),
                            source=self.id,
                            source_url=bank.get("source_url"),
                            linked_series=[bank["rate_series"]]
                            if bank.get("rate_series")
                            else (ind.linked_series if ind else []),
                            notes="; ".join(note_bits) or None,
                            tier=self.tier,
                            verified=verified,
                            tags=tags,
                            raw={
                                "bank": bank["bank"],
                                "sep": bool(d.get("sep")),
                                "start": d.get("start").isoformat() if d.get("start") else None,
                            },
                        )
                    )
                if bank.get("minutes_rule") == "fomc":
                    md = fomc_minutes_date(dd)
                    if start <= md <= end:
                        out.append(
                            NormalizedEvent(
                                event_key="us.fomc_minutes",
                                kind="cb_minutes",
                                country=cc,
                                currency="USD",
                                title=f"FOMC minutes ({dd.strftime('%b %d')} meeting)",
                                ts=local_ts(md, "14:00", tz),
                                importance=2,
                                category="rates",
                                reference_period=dd.isoformat(),
                                source=self.id,
                                source_url=bank.get("source_url"),
                                notes="rule: decision + 3 weeks",
                                tier="rule",
                                verified=verified,
                                tags=tags,
                            )
                        )
                    bb = beige_book_date(dd)
                    if start <= bb <= end:
                        out.append(
                            NormalizedEvent(
                                event_key="us.beige_book",
                                kind="macro",
                                country=cc,
                                currency="USD",
                                title="Fed Beige Book",
                                ts=local_ts(bb, "14:00", tz),
                                importance=1,
                                category="growth",
                                reference_period=dd.isoformat(),
                                source=self.id,
                                source_url="https://www.federalreserve.gov/monetarypolicy/beige-book-default.htm",
                                notes="rule: two weeks before the FOMC meeting",
                                tier="rule",
                                verified=verified,
                                tags=tags,
                            )
                        )
        return out


def next_decisions(today: date | None = None) -> list[dict]:
    """Next scheduled decision per bank (for the CB strip)."""
    today = today or date.today()
    out = []
    for bank in load_central_banks():
        future = sorted(
            (d for d in bank.get("decisions") or [] if d["date"] >= today), key=lambda d: d["date"]
        )
        nxt = future[0] if future else None
        past = sorted((d for d in bank.get("decisions") or [] if d["date"] < today), key=lambda d: d["date"])
        out.append(
            {
                "bank": bank["bank"],
                "name": bank["name"],
                "country": bank["country"],
                "event_key": bank["event_key"],
                "rate_series": bank.get("rate_series"),
                "source_url": bank.get("source_url"),
                "next_date": nxt["date"].isoformat() if nxt else None,
                "next_ts": local_ts(nxt["date"], bank.get("decision_time", "12:00"), bank.get("tz", "UTC"))
                if nxt
                else None,
                "days_to_go": (nxt["date"] - today).days if nxt else None,
                "sep": bool(nxt.get("sep")) if nxt else False,
                "verified": bool(nxt.get("verified", True)) if nxt else None,
                "last_date": past[-1]["date"].isoformat() if past else None,
                "remaining_this_year": len([d for d in future if d["date"].year == today.year]),
            }
        )
    return out
