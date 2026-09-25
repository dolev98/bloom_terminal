"""Statements service: entities, ingestion (EDGAR XBRL, 6-K, filings.xbrl.org, PDF), resolution, ratios,
coverage, document approval. All DB access via session_scope; all provider HTTP via registry.call."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.data.registry import get_registry
from app.data.store.sqlite import session_scope
from app.statements import pdf_pipeline, sixk, xbrl_org
from app.statements.canonical import (
    CORE_FIELDS,
    Derivation,
    apply_derived,
    build_frame,
    frame_to_payload,
    prune_empty_periods,
    resolve,
)
from app.statements.concept_map import load_mapper
from app.statements.edgar_xbrl import companyfacts_to_facts, fye_month_from_submissions
from app.statements.fields import PERIOD_TYPES, STATEMENT_OF, FactRow
from app.statements.models import Entity, Fact, StatementDoc
from app.statements.ratios import compute_ratios
from app.statements.schemas import SIXK_SYSTEM, StatementsExtraction
from app.statements.validation import validate_extraction

log = logging.getLogger(__name__)

US_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT", "10-QT"}
FOREIGN_FORMS = {"20-F", "40-F", "20-F/A", "40-F/A"}
XBRL_FORMS = US_FORMS | FOREIGN_FORMS
FACT_COLUMNS = (
    "entity_id",
    "ticker",
    "canonical_field",
    "period_end",
    "period_type",
    "period_start",
    "fiscal_year",
    "fiscal_period",
    "value",
    "currency",
    "unit_scale",
    "source",
    "source_concept",
    "accession_or_url",
    "page",
    "filed_at",
    "restated_flag",
    "confidence",
    "approved",
    "doc_id",
    "schema_version",
)
KEY_COLUMNS = ("entity_id", "canonical_field", "period_end", "period_type", "source", "restated_flag")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- entities ------------------------------------------------------------------------------------------------


def capabilities(entity: Entity | dict) -> dict:
    ft = entity["filer_type"] if isinstance(entity, dict) else entity.filer_type
    return {
        "xbrl_annual": ft in ("us_10k", "foreign_20f", "ifrs_esef"),
        "xbrl_quarterly": ft == "us_10k",
        "xbrl_half_year": ft == "ifrs_esef",
        "sixk_parsed": ft == "foreign_20f",
        "pdf_llm": True,
    }


def entity_dict(e: Entity) -> dict:
    d = {
        "entity_id": e.entity_id,
        "ticker": e.ticker,
        "name": e.name,
        "filer_type": e.filer_type,
        "cik": e.cik,
        "lei": e.lei,
        "currency": e.currency,
        "fiscal_year_end_month": e.fiscal_year_end_month,
        "taxonomies": e.taxonomies or [],
        "updated_at": e.updated_at,
    }
    d["capabilities"] = capabilities(d)
    return d


async def get_entity(ticker: str) -> Entity | None:
    t = ticker.strip().upper()
    async with session_scope() as s:
        row = (await s.execute(select(Entity).where(Entity.ticker == t))).scalar_one_or_none()
        if row is None:
            row = await s.get(Entity, t)
        return row


def classify_forms(forms: list[str]) -> str:
    fs = set(forms)
    if fs & US_FORMS:
        return "us_10k"
    if fs & FOREIGN_FORMS:
        return "foreign_20f"
    return "unknown"


async def ensure_entity(ticker: str, *, refresh: bool = False) -> Entity:
    """Classify the filer from SEC submissions (10-K vs 20-F/40-F); `.TA` suffix -> tase_only (no network)."""
    t = ticker.strip().upper()
    existing = await get_entity(t)
    if existing is not None and not refresh:
        return existing
    if t.endswith(".TA"):
        values = {
            "entity_id": f"tase:{t[:-3]}",
            "ticker": t,
            "name": t[:-3],
            "filer_type": "tase_only",
            "currency": "ILS",
            "fiscal_year_end_month": 12,
        }
    else:
        reg = get_registry()
        edgar = reg.get("edgar")
        sub = await reg.call(edgar, "submissions", lambda: edgar.submissions(t), key=t)
        cik = int(sub.get("cik") or 0)
        forms = list(sub.get("filings", {}).get("recent", {}).get("form", []))
        values = {
            "entity_id": f"cik:{cik}",
            "ticker": t,
            "name": sub.get("name") or t,
            "filer_type": classify_forms(forms),
            "cik": cik,
            "fiscal_year_end_month": fye_month_from_submissions(sub),
            "currency": "USD",
        }
    async with session_scope() as s:
        row = await s.get(Entity, values["entity_id"])
        if row is None:
            row = Entity(**values)
            s.add(row)
        else:
            for k, v in values.items():
                if v is not None:
                    setattr(row, k, v)
        row.updated_at = _now()
        await s.flush()
        return row


async def _update_entity(entity_id: str, **fields) -> None:
    async with session_scope() as s:
        row = await s.get(Entity, entity_id)
        if row is None:
            return
        for k, v in fields.items():
            if v is not None:
                setattr(row, k, v)
        row.updated_at = _now()


# --- facts ---------------------------------------------------------------------------------------------------


def _fact_values(r: FactRow) -> dict:
    return {c: getattr(r, c) for c in FACT_COLUMNS}


async def load_facts(
    entity_id: str, period_type: str | None = None, include_derived: bool = True
) -> list[Fact]:
    async with session_scope() as s:
        q = select(Fact).where(Fact.entity_id == entity_id)
        if period_type:
            q = q.where(Fact.period_type == period_type)
        if not include_derived:
            q = q.where(Fact.source != "derived")
        return list((await s.execute(q)).scalars().all())


async def upsert_facts(rows: list[FactRow], *, keep_point_in_time: bool = False) -> dict:
    """Insert-or-update by the unique key. With keep_point_in_time, a newer filing that changes a stored
    point-in-time value is written as the restated row instead of overwriting it."""
    if not rows:
        return {"inserted": 0, "updated": 0, "restated": 0}
    entity_ids = {r.entity_id for r in rows}
    sources = {r.source for r in rows}
    async with session_scope() as s:
        existing = (
            await s.execute(
                select(
                    Fact.entity_id,
                    Fact.canonical_field,
                    Fact.period_end,
                    Fact.period_type,
                    Fact.source,
                    Fact.restated_flag,
                    Fact.value,
                    Fact.filed_at,
                ).where(Fact.entity_id.in_(entity_ids), Fact.source.in_(sources))
            )
        ).all()
    ex: dict[tuple, tuple[float, date | None]] = {tuple(e[:6]): (e[6], e[7]) for e in existing}
    restated = 0
    out: dict[tuple, FactRow] = {}
    for r in rows:
        if keep_point_in_time and not r.restated_flag:
            cur = ex.get(r.key)
            if (
                cur
                and abs(cur[0] - r.value) > 1e-6 * max(1.0, abs(cur[0]))
                and r.filed_at
                and cur[1]
                and r.filed_at > cur[1]
            ):
                r.restated_flag = True
                restated += 1
        out[r.key] = r
    inserted = sum(1 for k in out if k not in ex)
    values = [_fact_values(r) for r in out.values()]
    async with session_scope() as s:
        for i in range(0, len(values), 400):
            stmt = sqlite_insert(Fact).values(values[i : i + 400])
            stmt = stmt.on_conflict_do_update(
                index_elements=list(KEY_COLUMNS),
                set_={c: getattr(stmt.excluded, c) for c in FACT_COLUMNS if c not in KEY_COLUMNS}
                | {"updated_at": _now()},
            )
            await s.execute(stmt)
    return {"inserted": inserted, "updated": len(out) - inserted, "restated": restated}


def _dominant_currency(rows: list[FactRow]) -> str | None:
    c = Counter(r.currency for r in rows if r.currency and STATEMENT_OF.get(r.canonical_field) == "BS")
    return c.most_common(1)[0][0] if c else None


# --- ingestion: EDGAR XBRL -----------------------------------------------------------------------------------


async def ingest_edgar(ticker: str, full: bool = False) -> dict:
    """companyfacts -> facts. `full` wipes the entity's edgar_xbrl rows first (clean re-ingest)."""
    entity = await ensure_entity(ticker)
    if entity.filer_type == "tase_only" or not entity.cik:
        return {
            "ticker": entity.ticker,
            "entity_id": entity.entity_id,
            "status": "skipped",
            "reason": entity.filer_type,
        }
    reg = get_registry()
    edgar = reg.get("edgar")
    cf = await reg.call(
        edgar, "companyfacts", lambda: edgar.companyfacts(str(entity.cik)), key=entity.ticker or ""
    )
    mapper = await load_mapper()
    rows = companyfacts_to_facts(
        cf, mapper, entity_id=entity.entity_id, ticker=entity.ticker, fye_month=entity.fiscal_year_end_month
    )
    if full:
        async with session_scope() as s:
            await s.execute(
                delete(Fact).where(Fact.entity_id == entity.entity_id, Fact.source == "edgar_xbrl")
            )
        pruned = 0
    else:
        pruned = await _prune_stale_edgar_rows(entity.entity_id, rows)
    counts = await upsert_facts(rows)
    taxonomies = [t for t in ("us-gaap", "ifrs-full") if t in cf.get("facts", {})]
    await _update_entity(entity.entity_id, taxonomies=taxonomies, currency=_dominant_currency(rows))
    derived = await recompute(entity.ticker or ticker)
    return {
        "ticker": entity.ticker,
        "entity_id": entity.entity_id,
        "status": "ok",
        "source": "edgar_xbrl",
        "rows": len(rows),
        **counts,
        "pruned": pruned,
        "derived": derived["rows"],
        "taxonomies": taxonomies,
    }


async def _prune_stale_edgar_rows(entity_id: str, rows: list[FactRow]) -> int:
    """companyfacts is the complete EDGAR picture for an entity, so stored edgar_xbrl rows whose key the current
    parse no longer produces are stale (e.g. rows keyed at 10-Q cover-page dates written before instants were
    snapped to the quarter end): they would otherwise shadow the corrected rows forever. Skipped when the parse
    looks truncated (fewer than half the stored rows) so a partial SEC response never wipes good data."""
    keep = {r.key for r in rows if r.source == "edgar_xbrl"}
    async with session_scope() as s:
        existing = (
            await s.execute(
                select(Fact.id, *(getattr(Fact, c) for c in KEY_COLUMNS)).where(
                    Fact.entity_id == entity_id, Fact.source == "edgar_xbrl"
                )
            )
        ).all()
        if not keep or len(keep) < len(existing) / 2:
            if existing:
                log.warning(
                    "edgar prune skipped for %s: %d parsed vs %d stored", entity_id, len(keep), len(existing)
                )
            return 0
        stale = [e[0] for e in existing if tuple(e[1:]) not in keep]
        for i in range(0, len(stale), 500):
            await s.execute(delete(Fact).where(Fact.id.in_(stale[i : i + 500])))
    return len(stale)


# --- ingestion: filings.xbrl.org --------------------------------------------------------------------------------


async def ingest_xbrl_org(lei_or_name: str, ticker: str | None = None, limit: int = 5) -> dict:
    is_lei = len(lei_or_name) == 20 and lei_or_name.isalnum()
    filings = await xbrl_org.search_filings(
        lei=lei_or_name if is_lei else None, name=None if is_lei else lei_or_name, limit=limit
    )
    if not filings:
        return {"status": "empty", "query": lei_or_name}
    lei = filings[0]["lei"] or lei_or_name
    entity_id = f"lei:{lei}"
    t = (ticker or lei).upper()
    async with session_scope() as s:
        row = await s.get(Entity, entity_id)
        if row is None:
            row = Entity(
                entity_id=entity_id,
                ticker=t,
                name=filings[0].get("entity_name") or t,
                filer_type="ifrs_esef",
                lei=lei,
                currency="EUR",
                fiscal_year_end_month=12,
                taxonomies=["ifrs-full"],
            )
            s.add(row)
        elif ticker:
            row.ticker = t
    mapper = await load_mapper()
    total = {"inserted": 0, "updated": 0, "restated": 0}
    used = []
    for f in sorted(filings, key=lambda x: x.get("date_added") or ""):
        if not f.get("json_url"):
            continue
        doc = await xbrl_org.fetch_facts_json(f["json_url"])
        filed = None
        if f.get("date_added"):
            try:
                filed = date.fromisoformat(f["date_added"][:10])
            except ValueError:
                filed = None
        rows = xbrl_org.xbrl_json_to_facts(
            doc, mapper, entity_id=entity_id, ticker=t, url=f["json_url"], filed_at=filed
        )
        c = await upsert_facts(rows, keep_point_in_time=True)
        for k in total:
            total[k] += c[k]
        used.append({"fxo_id": f.get("fxo_id"), "period_end": f.get("period_end"), "rows": len(rows)})
        cur = _dominant_currency(rows)
        if cur:
            await _update_entity(entity_id, currency=cur)
    derived = await recompute(t)
    return {
        "ticker": t,
        "entity_id": entity_id,
        "status": "ok",
        "source": "xbrl_org",
        "filings": used,
        **total,
        "derived": derived["rows"],
    }


# --- ingestion: 6-K --------------------------------------------------------------------------------------------


def _group_periods(rows: list[FactRow]) -> list[dict]:
    by: dict[tuple[str, date], dict] = {}
    for r in rows:
        by.setdefault((r.period_type, r.period_end), {})[r.canonical_field] = r.value
    return [{"period_end": k[1].isoformat(), "period_type": k[0], "values": v} for k, v in sorted(by.items())]


async def _existing_values(entity_id: str, ends: set[str]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    if not ends:
        return out
    facts = await load_facts(entity_id, include_derived=False)
    for f in facts:
        if f.approved and f.period_end.isoformat() in ends and f.period_type in ("FY", "Q", "H"):
            out.setdefault(f.period_end.isoformat(), {}).setdefault(f.canonical_field, f.value)
    return out


async def _doc_by_url(url: str) -> StatementDoc | None:
    async with session_scope() as s:
        return (
            await s.execute(select(StatementDoc).where(StatementDoc.path_or_url == url))
        ).scalar_one_or_none()


def doc_dict(d: StatementDoc) -> dict:
    return {
        "id": d.id,
        "entity_id": d.entity_id,
        "ticker": d.ticker,
        "kind": d.kind,
        "path_or_url": d.path_or_url,
        "status": d.status,
        "fiscal_year": d.fiscal_year,
        "period_type": d.period_type,
        "source": d.source,
        "extracted": d.extracted,
        "validation": d.validation,
        "llm_usage": d.llm_usage,
        "created_at": d.created_at,
        "decided_at": d.decided_at,
    }


async def ingest_6k(
    ticker: str,
    accession: str,
    *,
    filed_at: date | None = None,
    parse_fn=None,
    html_docs: list[tuple[str, str, str]] | None = None,
    force: bool = False,
) -> dict:
    """Parse the financial exhibit of a 6-K (tables -> 6k_parsed; LLM fallback -> 6k_llm, pending)."""
    entity = await ensure_entity(ticker)
    if not entity.cik and html_docs is None:
        return {"status": "skipped", "reason": "no CIK"}
    docs = html_docs if html_docs is not None else await sixk.fetch_exhibits(entity.cik or 0, accession)
    if not docs:
        return {"status": "empty", "accession": accession}
    parsed = [(name, url, html, sixk.find_statement_tables(html)) for name, url, html in docs]
    name, url, html, tables = max(parsed, key=lambda t: len(t[3]))
    existing = await _doc_by_url(url)
    if existing is not None and not force and existing.status != "failed":
        return {"status": "exists", "doc": doc_dict(existing)}
    mapper = await load_mapper()
    fye = entity.fiscal_year_end_month or 12
    async with session_scope() as s:
        doc = existing or StatementDoc(
            entity_id=entity.entity_id,
            ticker=entity.ticker,
            kind="6k",
            path_or_url=url,
            status="pending",
            period_type="Q",
            source="6k_parsed",
        )
        if existing is None:
            s.add(doc)
        await s.flush()
        doc_id = doc.id
    rows, meta = sixk.tables_to_facts(
        tables,
        mapper,
        entity_id=entity.entity_id,
        ticker=entity.ticker,
        fye_month=fye,
        accession=accession,
        filed_at=filed_at,
        doc_id=doc_id,
    )
    kinds = {t.kind for t in tables}
    source, usage, extracted = "6k_parsed", None, meta
    if len(kinds) < 2 or len(rows) < 6:
        text = sixk.html_text(html)
        if len({k for k in ("IS", "BS", "CF") if sixk.KIND_PATTERNS[k].search(text)}) >= 2:
            try:
                from app.llm.client import text_block

                if parse_fn is None:
                    from app.llm import client as llm

                    parse_fn = llm.parse
                extraction, usage = await parse_fn(
                    StatementsExtraction,
                    SIXK_SYSTEM,
                    [text_block(text)],
                    model="claude-sonnet-5",
                    purpose="statements_6k",
                    max_tokens=16000,
                    estimated_usd=0.15,
                )
                rows = pdf_pipeline.extraction_to_facts(
                    extraction,
                    entity_id=entity.entity_id,
                    ticker=entity.ticker,
                    source="6k_llm",
                    path_or_url=url,
                    doc_id=doc_id,
                    confidence=0.6,
                    filed_at=filed_at,
                )
                source, extracted = "6k_llm", extraction.model_dump()
            except Exception as e:
                log.warning("6-K LLM fallback failed for %s: %s", accession, e)
                async with session_scope() as s:
                    d = await s.get(StatementDoc, doc_id)
                    if d:
                        d.status, d.validation, d.source = (
                            "failed",
                            {"error": f"{type(e).__name__}: {e}"[:500], "parser": meta},
                            "6k_llm",
                        )
                    return {"status": "failed", "doc": doc_dict(d) if d else None}
    periods = _group_periods(rows)
    validation = validate_extraction(
        periods, await _existing_values(entity.entity_id, {p["period_end"] for p in periods})
    )
    approved = source == "6k_parsed" and validation["passed"]
    for r in rows:
        r.approved = approved
        r.doc_id = doc_id
    fy = Counter(r.fiscal_year for r in rows if r.fiscal_year).most_common(1)
    ptype = Counter(r.period_type for r in rows if r.period_type in ("Q", "H", "FY")).most_common(1)
    counts = await upsert_facts(rows, keep_point_in_time=True)
    async with session_scope() as s:
        d = await s.get(StatementDoc, doc_id)
        assert d is not None
        d.status = "approved" if approved else ("pending" if rows else "failed")
        d.decided_at = _now() if approved else None
        d.source = source
        d.extracted = extracted
        d.validation = validation | {"exhibit": name}
        d.llm_usage = usage
        d.fiscal_year = fy[0][0] if fy else None
        d.period_type = ptype[0][0] if ptype else "Q"
        result = doc_dict(d)
    await recompute(entity.ticker or ticker)
    return {"status": result["status"], "source": source, "rows": len(rows), **counts, "doc": result}


async def latest_financial_6ks(ticker: str, limit: int = 4) -> list[dict]:
    entity = await ensure_entity(ticker)
    if not entity.cik:
        return []
    reg = get_registry()
    edgar = reg.get("edgar")
    recent = await reg.call(
        edgar,
        "recent_filings",
        lambda: edgar.recent_filings(str(entity.cik), forms={"6-K"}, limit=limit * 3),
        key=ticker,
    )
    return recent[:limit]


# --- ingestion: PDF --------------------------------------------------------------------------------------------


async def ingest_pdf(
    path: Path | str,
    ticker: str,
    fiscal_year: int | None,
    period_type: str | None,
    *,
    parse_fn=None,
    texts: list[str] | None = None,
) -> dict:
    entity = await ensure_entity(ticker)
    path = Path(path)
    async with session_scope() as s:
        doc = StatementDoc(
            entity_id=entity.entity_id,
            ticker=entity.ticker,
            kind="pdf",
            path_or_url=str(path),
            status="pending",
            fiscal_year=fiscal_year,
            period_type=period_type,
            source="pdf_llm",
        )
        s.add(doc)
        await s.flush()
        doc_id = doc.id
    try:
        res = await pdf_pipeline.extract(
            path,
            ticker=entity.ticker or ticker,
            fiscal_year=fiscal_year,
            period_type=period_type,
            parse_fn=parse_fn,
            texts=texts,
        )
        extraction: StatementsExtraction = res["extraction"]
        rows = pdf_pipeline.extraction_to_facts(
            extraction,
            entity_id=entity.entity_id,
            ticker=entity.ticker,
            path_or_url=str(path),
            doc_id=doc_id,
            page_map=res["page_map"],
        )
        periods = pdf_pipeline.extraction_periods(extraction)
        validation = validate_extraction(
            periods, await _existing_values(entity.entity_id, {p["period_end"] for p in periods})
        )
        located = res["located"]
        counts = await upsert_facts(rows)
        async with session_scope() as s:
            d = await s.get(StatementDoc, doc_id)
            assert d is not None
            d.extracted = extraction.model_dump()
            d.validation = validation | {
                "pages": [p + 1 for p in located.pages],
                "kinds": {str(k + 1): v for k, v in located.kinds.items()},
                "unit_scale": located.unit_scale,
                "currency": located.currency,
            }
            d.llm_usage = res["usage"]
            d.status = "pending" if rows else "failed"
            result = doc_dict(d)
        await recompute(entity.ticker or ticker)
        return {"status": result["status"], "rows": len(rows), **counts, "doc": result}
    except Exception as e:
        log.exception("pdf ingest failed for %s", path)
        async with session_scope() as s:
            d = await s.get(StatementDoc, doc_id)
            if d:
                d.status = "failed"
                d.validation = {"error": f"{type(e).__name__}: {e}"[:500]}
                result = doc_dict(d)
        return {"status": "failed", "error": f"{type(e).__name__}: {e}"[:500], "doc": result}


# --- documents --------------------------------------------------------------------------------------------------


async def list_docs(status: str | None = None, ticker: str | None = None) -> list[dict]:
    async with session_scope() as s:
        q = select(StatementDoc).order_by(StatementDoc.created_at.desc())
        if status:
            q = q.where(StatementDoc.status == status)
        if ticker:
            q = q.where(StatementDoc.ticker == ticker.upper())
        return [doc_dict(d) for d in (await s.execute(q)).scalars().all()]


async def decide_doc(doc_id: int, approve: bool) -> dict:
    async with session_scope() as s:
        d = await s.get(StatementDoc, doc_id)
        if d is None:
            raise KeyError(doc_id)
        d.status = "approved" if approve else "rejected"
        d.decided_at = _now()
        if approve:
            facts = (await s.execute(select(Fact).where(Fact.doc_id == doc_id))).scalars().all()
            for f in facts:
                f.approved = True
        else:
            await s.execute(delete(Fact).where(Fact.doc_id == doc_id))
        ticker = d.ticker
        result = doc_dict(d)
    if ticker:
        await recompute(ticker)
    return result


# --- derived / queries --------------------------------------------------------------------------------------------


def _derived_row(
    d: Derivation, entity_id: str, ticker: str | None, period_type: str, restated: bool, tmpl: Fact | FactRow
) -> FactRow:
    return FactRow(
        entity_id=entity_id,
        ticker=ticker,
        canonical_field=d.field,
        period_end=d.period_end,
        period_type=period_type,
        period_start=tmpl.period_start,
        fiscal_year=tmpl.fiscal_year,
        fiscal_period=tmpl.fiscal_period,
        value=d.value,
        currency=tmpl.currency,
        unit_scale=0,
        source="derived",
        source_concept=f"formula:{d.formula}",
        accession_or_url=tmpl.accession_or_url,
        filed_at=max((i.filed_at for i in d.inputs if i.filed_at), default=None),
        restated_flag=restated,
        confidence=min((i.confidence for i in d.inputs), default=1.0),
        approved=all(i.approved for i in d.inputs),
        doc_id=None,
    )


async def recompute(ticker: str) -> dict:
    """Re-derive gross_profit / total_liabilities / ebitda / fcf rows (source=derived) from the resolved facts."""
    entity = await get_entity(ticker)
    if entity is None:
        return {"rows": 0}
    facts = await load_facts(entity.entity_id, include_derived=False)
    async with session_scope() as s:
        await s.execute(delete(Fact).where(Fact.entity_id == entity.entity_id, Fact.source == "derived"))
    rows: list[FactRow] = []
    for ptype in PERIOD_TYPES:
        pit_vals: dict[tuple[str, date], float] = {}
        for restated in (False, True):
            resolved = resolve(facts, period_type=ptype, restated=restated, approved_only=False)
            if not resolved:
                continue
            frame = build_frame(resolved, ptype)
            _, derivs = apply_derived(frame, resolved)
            for d in derivs:
                if not d.inputs:
                    continue
                if restated:
                    if (d.field, d.period_end) in pit_vals and abs(
                        pit_vals[(d.field, d.period_end)] - d.value
                    ) <= 1e-6 * max(1.0, abs(d.value)):
                        continue
                else:
                    pit_vals[(d.field, d.period_end)] = d.value
                rows.append(_derived_row(d, entity.entity_id, entity.ticker, ptype, restated, d.inputs[0]))
    counts = await upsert_facts(rows) if rows else {"inserted": 0, "updated": 0, "restated": 0}
    return {"rows": len(rows), **counts}


async def get_statements(
    ticker: str, period_type: str = "FY", restated: bool = True, approved_only: bool = True
) -> dict:
    entity = await get_entity(ticker)
    if entity is None:
        raise KeyError(ticker)
    facts = await load_facts(entity.entity_id, period_type)
    resolved = prune_empty_periods(
        resolve(facts, period_type=period_type, restated=restated, approved_only=approved_only)
    )
    frame = build_frame(resolved, period_type)
    frame, derivs = apply_derived(frame, resolved)
    payload = frame_to_payload(frame, resolved, derivs)
    return {
        "ticker": entity.ticker,
        "entity_id": entity.entity_id,
        "period_type": period_type,
        "restated": restated,
        "approved_only": approved_only,
        **payload,
    }


async def ratios(
    ticker: str,
    period_type: str = "FY",
    restated: bool = True,
    approved_only: bool = True,
    market_cap: float | None = None,
) -> dict:
    entity = await get_entity(ticker)
    if entity is None:
        raise KeyError(ticker)
    facts = await load_facts(entity.entity_id, period_type)
    resolved = prune_empty_periods(
        resolve(facts, period_type=period_type, restated=restated, approved_only=approved_only)
    )
    frame = build_frame(resolved, period_type)
    frame, _ = apply_derived(frame, resolved)
    return {
        "ticker": entity.ticker,
        "period_type": period_type,
        "market_cap": market_cap,
        "periods": compute_ratios(frame, period_type, market_cap),
    }


async def coverage(ticker: str) -> list[dict]:
    """Period coverage strip: one row per (period_type, period_end) with per-source counts."""
    entity = await get_entity(ticker)
    if entity is None:
        raise KeyError(ticker)
    facts = await load_facts(entity.entity_id)
    by: dict[tuple[str, date], dict] = {}
    for f in facts:
        row = by.setdefault(
            (f.period_type, f.period_end),
            {
                "period_type": f.period_type,
                "period_end": f.period_end,
                "fiscal_year": f.fiscal_year,
                "fiscal_period": f.fiscal_period,
                "sources": {},
            },
        )
        if f.canonical_field in CORE_FIELDS:
            row["has_core"] = True
        src = row["sources"].setdefault(
            f.source,
            {
                "source": f.source,
                "n_fields": 0,
                "approved": 0,
                "pending": 0,
                "restated": False,
                "filed_at": None,
                "accession_or_url": f.accession_or_url,
            },
        )
        src["n_fields"] += 1
        src["approved" if f.approved else "pending"] += 1
        src["restated"] = src["restated"] or bool(f.restated_flag)
        if f.filed_at and (src["filed_at"] is None or f.filed_at > src["filed_at"]):
            src["filed_at"] = f.filed_at
    out = []
    order = {"FY": 0, "Q": 1, "H": 2, "TTM": 3}
    for (_ptype, _pend), row in sorted(
        by.items(), key=lambda kv: (kv[0][1], order.get(kv[0][0], 9)), reverse=True
    ):
        if not row.pop("has_core", False):
            continue  # no core financial content (e.g. a lone share count)
        out.append({**row, "sources": sorted(row["sources"].values(), key=lambda s: s["source"])})
    return out


async def stale_entities(older_than: timedelta) -> list[str]:
    async with session_scope() as s:
        rows = (
            (await s.execute(select(Entity.ticker).where(Entity.updated_at < _now() - older_than)))
            .scalars()
            .all()
        )
    return [r for r in rows if r]
