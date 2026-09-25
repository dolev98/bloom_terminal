"""/api/valuation — runs, assumptions, imports, models registry, peers, macro, policies."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.valuation import service
from app.valuation.assumptions import Assumptions
from app.valuation.policies import get_policies, put_policies
from app.valuation.providers import damodaran, treasury
from app.valuation.registry import get_model_registry

router = APIRouter(prefix="/api/valuation", tags=["valuation"])
_FILE = File(None)


# --- registry / macro / policies (static paths first) --------------------------------------


@router.get("/models")
async def models() -> dict:
    reg = get_model_registry()
    return {
        "models": reg.describe(),
        "errors": reg.errors,
        "loaded_at": reg.loaded_at,
        "user_dir": str(reg.user_dir),
    }


@router.post("/models/reload")
async def reload_models() -> dict:
    return get_model_registry().reload_user_models()


@router.get("/macro")
async def macro(refresh: bool = False) -> dict:
    if refresh:
        await treasury.get_rf(max_age=__import__("datetime").timedelta(0))
        await damodaran.get_erp(max_age=__import__("datetime").timedelta(0))
        await damodaran.get_crp("Israel", max_age=__import__("datetime").timedelta(0))
    snap = await damodaran.macro_snapshot()
    snap["industry_stats"] = await damodaran.industry_stats()
    return snap


@router.post("/macro/industry/refresh")
async def refresh_industry() -> dict:
    return await damodaran.refresh_industry_stats()


@router.get("/policies")
async def policies() -> dict:
    return (await get_policies()).model_dump()


@router.put("/policies")
async def update_policies(body: dict[str, Any]) -> dict:
    return (await put_policies(body)).model_dump()


# --- per ticker -----------------------------------------------------------------------------


@router.get("/{ticker}")
async def get_valuation(ticker: str) -> dict:
    return await service.overview(ticker)


class RunRequest(BaseModel):
    model_ids: list[str] | None = None
    assumption_set_id: int | None = None
    assumptions: dict[str, Any] | None = Field(
        None, description="Assumption overrides -> creates a manual set"
    )
    scenario: str = "base"
    with_scenarios: bool = True
    with_sensitivity: bool = True


@router.post("/{ticker}/run")
async def run(ticker: str, req: RunRequest | None = None) -> dict:
    req = req or RunRequest()
    overrides = None
    if req.assumptions:
        try:
            overrides = Assumptions.model_validate(req.assumptions)
        except Exception as e:
            raise HTTPException(422, f"bad assumptions: {e}") from e
    try:
        return await service.run_valuation(
            ticker,
            req.model_ids,
            req.assumption_set_id,
            overrides,
            req.scenario,
            with_scenarios=req.with_scenarios,
            with_sensitivity=req.with_sensitivity,
        )
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/{ticker}/assumptions")
async def assumptions(ticker: str) -> dict:
    return {
        "ticker": ticker.upper(),
        "sets": await service.list_assumption_sets(ticker),
        "schema": Assumptions.json_schema(),
    }


class AssumptionSetRequest(BaseModel):
    assumptions: dict[str, Any] = Field(default_factory=dict)
    name: str = ""
    scenario: str | None = None
    parent_id: int | None = None
    note: str | None = None


@router.post("/{ticker}/assumptions")
async def create_assumptions(ticker: str, req: AssumptionSetRequest) -> dict:
    base = Assumptions()
    if req.parent_id is not None:
        parent = await service.get_assumption_set(req.parent_id)
        if parent is None:
            raise HTTPException(404, "parent set not found")
        base = Assumptions.model_validate(parent["payload"])
    try:
        a = base.merged(req.assumptions)
    except Exception as e:
        raise HTTPException(422, f"bad assumptions: {e}") from e
    return await service.create_assumption_set(
        ticker, a, req.name, req.scenario, "manual", req.parent_id, req.note
    )


@router.get("/{ticker}/assumptions/auto")
async def auto(ticker: str, persist: bool = False) -> dict:
    return await service.auto_assumptions(ticker, persist=persist)


@router.get("/{ticker}/assumptions/{set_id}/diff")
async def diff(ticker: str, set_id: int) -> dict:
    try:
        return await service.diff_assumption_set(set_id)
    except KeyError as e:
        raise HTTPException(404, "set not found") from e


@router.post("/{ticker}/import")
async def import_assumptions(
    ticker: str,
    kind: str = Form("csv"),
    url: str | None = Form(None),
    file: UploadFile | None = _FILE,
    sheet_cell_map: str | None = Form(None),
) -> dict:
    if kind not in ("csv", "xlsx", "sheets", "ginzu"):
        raise HTTPException(400, "kind must be csv|xlsx|sheets|ginzu")
    cell_map = None
    if sheet_cell_map:
        import json

        try:
            cell_map = json.loads(sheet_cell_map)
        except ValueError as e:
            raise HTTPException(400, f"bad cell map json: {e}") from e
    try:
        if file is not None:
            data = await file.read()
            return await service.import_assumptions(
                ticker, data, kind, filename=file.filename, cell_map=cell_map
            )
        if url:
            return await service.import_assumptions(
                ticker, url, "sheets" if kind == "csv" and "docs.google.com" in url else kind, filename=url
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"import failed: {type(e).__name__}: {e}"[:300]) from e
    raise HTTPException(400, "provide a file or a url")


@router.get("/{ticker}/peers")
async def peers(ticker: str, rebuild: bool = False) -> dict:
    if rebuild:
        return await service.build_peers(ticker)
    p = await service.get_peers(ticker)
    return p or {
        "ticker": ticker.upper(),
        "peers": [],
        "pins": [],
        "excludes": [],
        "effective": [],
        "sic": None,
        "industry": None,
    }


class PeersRequest(BaseModel):
    pins: list[str] | None = None
    excludes: list[str] | None = None
    industry: str | None = None


@router.put("/{ticker}/peers")
async def put_peers(ticker: str, req: PeersRequest) -> dict:
    return await service.set_peers(ticker, req.pins, req.excludes, req.industry)
