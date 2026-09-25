"""Statements jobs: weekly watchlist re-ingest and a 6-hourly poll for new 10-K/10-Q/20-F/6-K filings.
Both are idempotent (upserts keyed by the facts unique key; 6-K docs keyed by exhibit URL)."""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select

from app.api.routers.watchlists import all_tickers
from app.data.registry import get_registry
from app.data.store.sqlite import session_scope
from app.statements import service
from app.statements.models import Fact, StatementDoc

log = logging.getLogger(__name__)
POLL_FORMS = {"10-K", "10-Q", "20-F", "40-F", "6-K", "10-K/A", "10-Q/A", "20-F/A"}


def _edgar_ticker(t: str) -> bool:
    return not (t.startswith("^") or "=" in t or t.endswith("-USD") or t.endswith(".TA"))


async def refresh_watchlist_statements_job() -> list[dict]:
    """Weekly: ingest_edgar(full=False) for every watchlist ticker that can be an EDGAR filer."""
    out: list[dict] = []
    for t in await all_tickers():
        if not _edgar_ticker(t):
            continue
        try:
            r = await service.ingest_edgar(t, full=False)
            out.append({"ticker": t, "status": r.get("status", "ok"), "rows": r.get("rows", 0)})
        except Exception as e:
            out.append({"ticker": t, "status": "error", "error": f"{type(e).__name__}: {e}"[:300]})
    return out


async def _known_accessions(entity_id: str) -> set[str]:
    async with session_scope() as s:
        accs = set(
            (await s.execute(select(Fact.accession_or_url).where(Fact.entity_id == entity_id).distinct()))
            .scalars()
            .all()
        )
        urls = (
            (await s.execute(select(StatementDoc.path_or_url).where(StatementDoc.entity_id == entity_id)))
            .scalars()
            .all()
        )
    known = {a for a in accs if a}
    for u in urls:
        known.add(u)
        known.add(
            u.rstrip("/").split("/")[-2] if u.count("/") >= 2 else u
        )  # accession folder of 6-K exhibit URLs
    return known


async def poll_new_filings_job() -> list[dict]:
    """Every 6 h: if a new XBRL filing / 6-K appears in recent_filings for a known entity, ingest it."""
    reg = get_registry()
    edgar = reg.get("edgar")
    out: list[dict] = []
    for t in await all_tickers():
        if not _edgar_ticker(t):
            continue
        ent = await service.get_entity(t)
        if ent is None or not ent.cik:
            continue
        try:
            recent = await reg.call(
                edgar,
                "recent_filings",
                lambda cik=ent.cik: edgar.recent_filings(str(cik), forms=POLL_FORMS, limit=12),
                key=t,
            )
        except Exception as e:
            out.append({"ticker": t, "status": "error", "error": str(e)[:200]})
            continue
        known = await _known_accessions(ent.entity_id)
        new_xbrl = [f for f in recent if f["form"] != "6-K" and f["accession"] not in known]
        new_6k = [
            f
            for f in recent
            if f["form"] == "6-K"
            and f["accession"].replace("-", "") not in known
            and f["accession"] not in known
        ]
        try:
            if new_xbrl:
                r = await service.ingest_edgar(t)
                out.append(
                    {
                        "ticker": t,
                        "status": "ok",
                        "xbrl": [f["accession"] for f in new_xbrl],
                        "rows": r.get("rows", 0),
                    }
                )
            if ent.filer_type == "foreign_20f":
                for f in new_6k[:3]:
                    filed = date.fromisoformat(f["filed"]) if f.get("filed") else None
                    r = await service.ingest_6k(t, f["accession"], filed_at=filed)
                    out.append({"ticker": t, "status": r.get("status", "ok"), "sixk": f["accession"]})
        except Exception as e:
            out.append({"ticker": t, "status": "error", "error": f"{type(e).__name__}: {e}"[:300]})
    return out
