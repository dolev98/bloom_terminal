from __future__ import annotations

import asyncio
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.config import get_settings
from app.statements import concept_map, service

router = APIRouter(prefix="/api/statements", tags=["statements"])
PeriodType = Literal["FY", "Q", "H", "TTM"]


def _clean_ticker(t: str) -> str:
    t = t.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-^=]{1,20}", t):
        raise HTTPException(400, "bad ticker")
    return t


@router.get("/docs")
async def docs(status: str | None = "pending", ticker: str | None = None) -> list[dict]:
    return await service.list_docs(status=status or None, ticker=ticker)


@router.post("/docs/{doc_id}/approve")
async def approve_doc(doc_id: int) -> dict:
    try:
        return await service.decide_doc(doc_id, approve=True)
    except KeyError as e:
        raise HTTPException(404, "doc not found") from e


@router.post("/docs/{doc_id}/reject")
async def reject_doc(doc_id: int) -> dict:
    try:
        return await service.decide_doc(doc_id, approve=False)
    except KeyError as e:
        raise HTTPException(404, "doc not found") from e


@router.get("/concept-map")
async def get_concept_map(taxonomy: str | None = None) -> list[dict]:
    return await concept_map.list_rows(taxonomy)


class ConceptMapRow(BaseModel):
    source_taxonomy: str
    source_concept: str
    canonical_field: str
    priority: int = 1
    sign: int = 1
    valid_from: date | None = None
    valid_to: date | None = None
    entity_override: str | None = None
    recompute_ticker: str | None = None


@router.put("/concept-map")
async def put_concept_map(row: ConceptMapRow) -> dict:
    from app.statements.fields import ALL_FIELDS

    if row.canonical_field not in ALL_FIELDS and not row.canonical_field.startswith("_"):
        raise HTTPException(400, f"unknown canonical field {row.canonical_field!r}")
    if row.sign not in (1, -1):
        raise HTTPException(400, "sign must be 1 or -1")
    saved = await concept_map.upsert_row(
        row.source_taxonomy,
        row.source_concept,
        row.canonical_field,
        row.priority,
        row.sign,
        row.valid_from,
        row.valid_to,
        row.entity_override,
    )
    recomputed = None
    if row.recompute_ticker:
        t = _clean_ticker(row.recompute_ticker)
        ent = await service.get_entity(t)
        if ent is not None and ent.filer_type in ("us_10k", "foreign_20f"):
            recomputed = await service.ingest_edgar(t)  # re-map XBRL facts under the new mapping
        else:
            recomputed = await service.recompute(t)
    return {"row": saved, "recomputed": recomputed}


@router.get("/{ticker}/entity")
async def entity(ticker: str, refresh: bool = False) -> dict:
    t = _clean_ticker(ticker)
    try:
        e = await service.ensure_entity(t, refresh=refresh)
    except Exception as e_:
        raise HTTPException(502, f"entity lookup failed: {e_}") from e_
    return service.entity_dict(e)


@router.get("/{ticker}/ratios")
async def ratios(
    ticker: str,
    period: PeriodType = "FY",
    restated: bool = True,
    approved_only: bool = True,
    market_cap: float | None = None,
) -> dict:
    try:
        return await service.ratios(_clean_ticker(ticker), period, restated, approved_only, market_cap)
    except KeyError as e:
        raise HTTPException(404, "unknown entity; POST /refresh first") from e


@router.get("/{ticker}/coverage")
async def coverage(ticker: str) -> list[dict]:
    try:
        return await service.coverage(_clean_ticker(ticker))
    except KeyError as e:
        raise HTTPException(404, "unknown entity; POST /refresh first") from e


@router.post("/{ticker}/refresh")
async def refresh(ticker: str, full: bool = False, sixk: bool = True, limit: int = 4) -> dict:
    """ingest_edgar for every EDGAR filer; for 20-F/40-F filers also parse the latest 6-Ks with financial exhibits."""
    t = _clean_ticker(ticker)
    out: dict = {"ticker": t}
    try:
        out["edgar"] = await service.ingest_edgar(t, full=full)
    except Exception as e:
        raise HTTPException(502, f"EDGAR ingest failed: {e}") from e
    ent = await service.get_entity(t)
    if sixk and ent is not None and ent.filer_type == "foreign_20f":
        results = []
        for f in await service.latest_financial_6ks(t, limit=limit):
            filed = date.fromisoformat(f["filed"]) if f.get("filed") else None
            try:
                results.append(
                    {
                        "accession": f["accession"],
                        **(await service.ingest_6k(t, f["accession"], filed_at=filed)),
                    }
                )
            except Exception as e:  # one bad exhibit must not fail the refresh
                results.append(
                    {
                        "accession": f["accession"],
                        "status": "error",
                        "error": f"{type(e).__name__}: {e}"[:300],
                    }
                )
        out["sixk"] = results
    return out


def _save_pdf(pdf_dir: Path, name: str, data: bytes) -> Path:
    pdf_dir.mkdir(parents=True, exist_ok=True)
    path = pdf_dir / name
    path.write_bytes(data)
    return path


@router.post("/{ticker}/pdf")
async def upload_pdf(
    ticker: str,
    file: Annotated[UploadFile, File()],
    fiscal_year: Annotated[int | None, Form()] = None,
    period_type: Annotated[str, Form()] = "FY",
) -> dict:
    t = _clean_ticker(ticker)
    if period_type not in ("FY", "Q", "H"):
        raise HTTPException(400, "period_type must be FY, Q or H")
    data = await file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "not a PDF")
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", t)
    name = f"{safe}_{fiscal_year or 'na'}_{period_type}_{stamp}.pdf"
    path = await asyncio.to_thread(_save_pdf, get_settings().pdf_dir, name, data)
    res = await service.ingest_pdf(path, t, fiscal_year, period_type)
    doc = res.get("doc") or {}
    return {
        "doc_id": doc.get("id"),
        "status": res.get("status"),
        "rows": res.get("rows", 0),
        "validation": doc.get("validation"),
        "error": res.get("error"),
    }


@router.get("/{ticker}")
async def statements(
    ticker: str, period: PeriodType = "FY", restated: bool = True, approved_only: bool = True
) -> dict:
    try:
        return await service.get_statements(_clean_ticker(ticker), period, restated, approved_only)
    except KeyError as e:
        raise HTTPException(404, "unknown entity; POST /refresh first") from e
