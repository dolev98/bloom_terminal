"""Valuation service: build inputs -> resolve assumptions -> run models -> store runs + fair_value_daily -> catalog series.

Public API (used by the router, jobs and the alerts engine):
  run_valuation, auto_assumptions, list_assumption_sets, create_assumption_set, import_assumptions,
  overview, latest_fair_values, reference_value, build_peers, get_peers, set_peers, own_history_stats.
"""

from __future__ import annotations

import inspect
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from sqlalchemy import select

from app.data.catalog.loader import get_spec, upsert_spec
from app.data.providers.base import SeriesSpec
from app.data.registry import get_registry
from app.data.series_service import _update_meta, write_manual
from app.data.store.sqlite import session_scope
from app.valuation.analysis import blended as blended_mod
from app.valuation.analysis import football_field, sensitivity
from app.valuation.analysis import scenarios as scen_mod
from app.valuation.assumptions import Assumptions
from app.valuation.inputs import InputsSnapshot, build_inputs
from app.valuation.models.base import ValuationResult
from app.valuation.models.external import load_external
from app.valuation.models.ginzu import GinzuImporter
from app.valuation.models.multiples import current_multiples
from app.valuation.orm import AssumptionSet, FairValueDaily, PeerSet, ValuationRun
from app.valuation.policies import Policies, get_policies
from app.valuation.registry import get_model_registry, reference_to_model

log = logging.getLogger(__name__)
DEFAULT_MODELS = ["fcff", "reverse_dcf", "multiples"]


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _run_model(model: Any, inputs: InputsSnapshot, a: Assumptions, pol: Policies) -> ValuationResult:
    try:
        accepts = "policies" in inspect.signature(model.run).parameters
    except (TypeError, ValueError):
        accepts = False
    res = model.run(inputs, a, pol) if accepts else model.run(inputs, a)
    if not isinstance(res, ValuationResult):
        res = ValuationResult.model_validate(res if isinstance(res, dict) else res.__dict__)
    return res


# --- assumption sets -----------------------------------------------------------------


def _set_to_dict(row: AssumptionSet) -> dict:
    return {
        "id": row.id,
        "ticker": row.ticker,
        "name": row.name,
        "scenario": row.scenario,
        "source": row.source,
        "parent_id": row.parent_id,
        "payload": row.payload,
        "checksum": row.checksum,
        "note": row.note,
        "created_at": row.created_at,
    }


async def create_assumption_set(
    ticker: str,
    a: Assumptions | dict,
    name: str = "",
    scenario: str | None = None,
    source: str = "manual",
    parent_id: int | None = None,
    note: str | None = None,
) -> dict:
    a = a if isinstance(a, Assumptions) else Assumptions.model_validate(a)
    if scenario:
        a.scenario = scenario  # type: ignore[assignment]
    async with session_scope() as s:
        row = AssumptionSet(
            ticker=ticker.upper(),
            name=name or f"{source} {_now():%Y-%m-%d %H:%M}",
            scenario=a.scenario or "base",
            source=source,
            parent_id=parent_id,
            payload=a.model_dump(exclude_none=True, by_alias=True),
            checksum=a.checksum(),
            note=note,
        )
        s.add(row)
        await s.flush()
        out = _set_to_dict(row)
    return out


async def get_assumption_set(set_id: int) -> dict | None:
    async with session_scope() as s:
        row = await s.get(AssumptionSet, set_id)
        return _set_to_dict(row) if row else None


async def list_assumption_sets(ticker: str, limit: int = 50) -> list[dict]:
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(AssumptionSet)
                    .where(AssumptionSet.ticker == ticker.upper())
                    .order_by(AssumptionSet.id.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [_set_to_dict(r) for r in rows]


async def diff_assumption_set(set_id: int) -> dict:
    cur = await get_assumption_set(set_id)
    if cur is None:
        raise KeyError(set_id)
    if cur["parent_id"] is None:
        return {"id": set_id, "parent_id": None, "diff": {}}
    parent = await get_assumption_set(cur["parent_id"])
    a, b = (
        Assumptions.model_validate(parent["payload"] if parent else {}),
        Assumptions.model_validate(cur["payload"]),
    )
    return {"id": set_id, "parent_id": cur["parent_id"], "diff": a.diff(b)}


async def auto_assumptions(ticker: str, inputs: InputsSnapshot | None = None, persist: bool = True) -> dict:
    """Resolve defaults for the ticker and (optionally) store them as an `auto` assumption set."""
    inputs = inputs or await build_inputs(ticker)
    pol = await get_policies()
    a = Assumptions(scenario="base")
    r = a.resolve(inputs, pol)
    out: dict[str, Any] = {
        "resolved": r.model_dump(),
        "assumptions": a.model_dump(exclude_none=True, by_alias=True),
        "inputs": inputs.summary(),
    }
    if persist:
        row = await create_assumption_set(
            ticker,
            a,
            name=f"auto {inputs.as_of}",
            scenario="base",
            source="auto",
            note=f"inputs {inputs.content_hash}",
        )
        out["assumption_set"] = row
    return out


# --- running ------------------------------------------------------------------------


async def run_valuation(
    ticker: str,
    model_ids: list[str] | None = None,
    assumption_set_id: int | None = None,
    overrides: dict | Assumptions | None = None,
    scenario: str = "base",
    inputs: InputsSnapshot | None = None,
    store: bool = True,
    with_scenarios: bool = True,
    with_sensitivity: bool = True,
) -> dict:
    """Run models for `ticker`; store valuation_runs + fair_value_daily and update `val:<T>:*` catalog series."""
    ticker = ticker.upper()
    reg = get_model_registry()
    pol = await get_policies()
    inputs = inputs or await build_inputs(ticker)
    ids = model_ids or DEFAULT_MODELS
    unknown = [m for m in ids if not reg.has(m)]
    if unknown:
        raise KeyError(f"unknown models: {unknown}")

    base_a = Assumptions(scenario="base")
    set_row: dict | None = None
    if assumption_set_id is not None:
        set_row = await get_assumption_set(assumption_set_id)
        if set_row is None:
            raise KeyError(f"assumption set {assumption_set_id} not found")
        base_a = Assumptions.model_validate(set_row["payload"])
    if overrides:
        base_a = base_a.merged(overrides)
        if store:
            set_row = await create_assumption_set(
                ticker,
                base_a,
                name="manual override",
                scenario=scenario,
                source="manual",
                parent_id=assumption_set_id,
            )
    elif set_row is None and store:
        set_row = await create_assumption_set(
            ticker,
            base_a,
            name=f"auto {inputs.as_of}",
            scenario="base",
            source="auto",
            note=f"inputs {inputs.content_hash}",
        )
    base_a.scenario = scenario if scenario in ("bear", "base", "bull", "custom") else "base"  # type: ignore[assignment]
    if base_a.history_stats is None and "multiples" in ids:
        try:
            hs = own_history_stats(inputs)
            if hs:
                base_a.history_stats = hs
        except Exception as e:  # pragma: no cover - defensive
            log.debug("history stats failed: %s", e)
    if base_a.peer_stats is None and "multiples" in ids:
        ps = await peer_stats_for(ticker)
        if ps:
            base_a.peer_stats = ps

    results: dict[str, ValuationResult] = {}
    scen_results: dict[str, ValuationResult] = {}
    grid: dict | None = None
    errors: dict[str, str] = {}
    for mid in ids:
        model = reg.get(mid)
        try:
            results[mid] = _run_model(model, inputs, base_a, pol)
        except Exception as e:
            errors[mid] = f"{type(e).__name__}: {e}"[:300]
            log.warning("model %s failed for %s: %s", mid, ticker, e)
    if "fcff" in results and results["fcff"].value_per_share is not None:
        if with_scenarios:
            sets = scen_mod.derive_scenarios(base_a, inputs, pol)
            scen_results = scen_mod.run_scenarios(reg.get("fcff"), inputs, sets, pol)
        if with_sensitivity:
            try:
                grid = {
                    "wacc_g": sensitivity.wacc_g_grid(reg.get("fcff"), inputs, base_a, pol),
                    "growth_margin": sensitivity.growth_margin_grid(reg.get("fcff"), inputs, base_a, pol),
                }
            except Exception as e:  # pragma: no cover - defensive
                log.debug("sensitivity failed: %s", e)

    price = inputs.market.price
    refs: dict[str, float | None] = {}
    for mid, res in results.items():
        ref = getattr(reg.get(mid), "reference", None)
        if ref and res.value_per_share is not None:
            refs[ref] = res.value_per_share
    if inputs.consensus and inputs.consensus.target_mean:
        refs["analyst"] = inputs.consensus.target_mean
    blend = blended_mod.blend(refs)
    user_results = {mid: r for mid, r in results.items() if mid in reg.user_ids()}
    ff = football_field.rows(
        inputs, scen_results or None, results.get("multiples"), None, results.get("external"), user_results
    )
    if blend["value"] is not None:
        ff.append(
            {
                "method": "Blended",
                "low": blend["value"],
                "base": blend["value"],
                "high": blend["value"],
                "reference": "blended",
            }
        )

    out: dict[str, Any] = {
        "ticker": ticker,
        "as_of": inputs.as_of.isoformat(),
        "price": price,
        "price_source": inputs.market.price_source,
        "price_ts": inputs.market.price_ts.isoformat() if inputs.market.price_ts else None,
        "currency": inputs.currency,
        "assumption_set": set_row,
        "inputs": inputs.summary(),
        "results": {mid: {**r.model_dump(), "upside": r.upside(price)} for mid, r in results.items()},
        "scenarios": {
            k: {"value_per_share": v.value_per_share, "upside": v.upside(price), "warnings": v.warnings}
            for k, v in scen_results.items()
        },
        "blended": {**blend, "upside": (blend["value"] / price - 1) if blend["value"] and price else None},
        "football_field": ff,
        "sensitivity": grid,
        "errors": errors,
        "warnings": list(inputs.warnings),
    }
    if store:
        await _store(
            ticker,
            inputs,
            set_row["id"] if set_row else None,
            results,
            scen_results,
            blend["value"],
            grid,
            out,
        )
    return out


async def _store(
    ticker: str,
    inputs: InputsSnapshot,
    set_id: int | None,
    results: dict[str, ValuationResult],
    scen: dict[str, ValuationResult],
    blended_value: float | None,
    grid: dict | None,
    out: dict,
) -> None:
    price, src, ts = inputs.market.price, inputs.market.price_source, inputs.market.price_ts
    today = _now().date()
    async with session_scope() as s:
        for mid, res in results.items():
            payload = res.model_dump()
            if mid == "fcff" and grid:
                payload["sensitivity"] = grid
                payload["scenarios"] = {k: v.value_per_share for k, v in scen.items()}
            s.add(
                ValuationRun(
                    ticker=ticker,
                    model_id=mid,
                    model_version=res.model_version,
                    scenario=res.scenario,
                    assumption_set_id=set_id,
                    inputs_snapshot_id=inputs.content_hash,
                    value_per_share=res.value_per_share,
                    price=price,
                    price_source=src,
                    price_ts=ts,
                    upside=res.upside(price),
                    status="ok" if res.value_per_share is not None else "error",
                    result=payload,
                    inputs_summary=inputs.summary(),
                )
            )
            await _upsert_fv(
                s,
                ticker,
                today,
                mid,
                res.scenario,
                res.value_per_share,
                price,
                src,
                ts,
                res.implied_growth,
                set_id,
                inputs,
            )
        for name, res in scen.items():
            if name != "base":
                await _upsert_fv(
                    s, ticker, today, "fcff", name, res.value_per_share, price, src, ts, None, set_id, inputs
                )
        if blended_value is not None:
            await _upsert_fv(
                s, ticker, today, "blended", "base", blended_value, price, src, ts, None, set_id, inputs
            )
        if inputs.consensus and inputs.consensus.target_mean:
            await _upsert_fv(
                s,
                ticker,
                today,
                "analyst",
                "base",
                inputs.consensus.target_mean,
                price,
                src,
                ts,
                None,
                None,
                inputs,
            )
    fv = (
        results["fcff"].value_per_share
        if "fcff" in results and results["fcff"].value_per_share is not None
        else blended_value
    )
    if fv is not None:
        try:
            await write_catalog_series(
                ticker, today, fv, (fv / price - 1) if price else None, inputs.currency
            )
        except Exception as e:  # pragma: no cover - defensive
            log.warning("catalog series write failed for %s: %s", ticker, e)


async def _upsert_fv(
    s,
    ticker: str,
    d: date,
    model: str,
    scenario: str,
    value: float | None,
    price,
    src,
    ts,
    implied_growth,
    set_id,
    inputs: InputsSnapshot,
) -> None:
    row = (
        await s.execute(
            select(FairValueDaily).where(
                FairValueDaily.ticker == ticker,
                FairValueDaily.date == d,
                FairValueDaily.model == model,
                FairValueDaily.scenario == scenario,
            )
        )
    ).scalar_one_or_none()
    upside = (value / price - 1) if value is not None and price else None
    vals = {
        "value": value,
        "price": price,
        "price_source": src,
        "price_ts": ts,
        "upside": upside,
        "implied_growth": implied_growth,
        "assumption_set_id": set_id,
        "inputs_snapshot_id": inputs.content_hash,
        "currency": inputs.currency,
    }
    if row is None:
        s.add(FairValueDaily(ticker=ticker, date=d, model=model, scenario=scenario, **vals))
    else:
        for k, v in vals.items():
            setattr(row, k, v)


async def write_catalog_series(
    ticker: str, d: date, fair_value: float, upside: float | None, currency: str = "USD"
) -> None:
    """`val:<T>:fair_value_base` and `val:<T>:upside` so charts/correlations can use them. Enabled only once the
    `val` provider is registered (otherwise refresh_stale would fail resolving the id)."""
    has_val = any(p.id == "val" for p in get_registry().all())
    specs = {
        f"val:{ticker}:fair_value_base": (
            "fair_value_base",
            f"{ticker} fair value (base DCF)",
            currency,
            "price",
            "log_ret",
            fair_value,
        ),
        f"val:{ticker}:upside": (
            "upside",
            f"{ticker} upside vs price (fraction)",
            "fraction",
            "ratio",
            "diff",
            upside,
        ),
    }
    ts = datetime(d.year, d.month, d.day)
    for sid, (fld, name, unit, kind, tr, value) in specs.items():
        if value is None:
            continue
        existing = await get_spec(sid)
        if existing is None or existing.enabled != has_val:
            await upsert_spec(
                SeriesSpec(
                    series_id=sid,
                    provider="val",
                    provider_key=ticker,
                    field_name=fld,
                    name=name,
                    freq="1d",
                    unit=unit,
                    value_kind=kind,
                    default_transform=tr,
                    category="valuation",
                    tags=["valuation", ticker],
                    enabled=has_val,
                    license_note="derived in-house",
                )
            )
        df = pl.DataFrame({"ts": [ts], "value": [float(value)]}).with_columns(
            pl.col("ts").cast(pl.Datetime("us"))
        )
        write_manual(sid, df)
        await _update_meta(sid, "ok", None, 1)


# --- reading ------------------------------------------------------------------------


async def latest_fair_values(ticker: str) -> list[dict]:
    """Latest row per (model, scenario)."""
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(FairValueDaily)
                    .where(FairValueDaily.ticker == ticker.upper())
                    .order_by(FairValueDaily.date.desc(), FairValueDaily.id.desc())
                )
            )
            .scalars()
            .all()
        )
    seen: set[tuple[str, str]] = set()
    out = []
    for r in rows:
        k = (r.model, r.scenario)
        if k in seen:
            continue
        seen.add(k)
        out.append(_fv_dict(r))
    return out


def _fv_dict(r: FairValueDaily) -> dict:
    return {
        "ticker": r.ticker,
        "date": r.date.isoformat(),
        "model": r.model,
        "scenario": r.scenario,
        "value": r.value,
        "price": r.price,
        "price_source": r.price_source,
        "price_ts": r.price_ts,
        "upside": r.upside,
        "implied_growth": r.implied_growth,
        "assumption_set_id": r.assumption_set_id,
        "inputs_snapshot_id": r.inputs_snapshot_id,
        "currency": r.currency,
    }


async def reference_value(ticker: str, reference: str, scenario: str = "base") -> dict | None:
    """Latest fair value for a *named reference* (base_dcf, imported_fv, multiples, blended, analyst, model:<id>)."""
    model = reference_to_model(reference)
    async with session_scope() as s:
        row = (
            await s.execute(
                select(FairValueDaily)
                .where(
                    FairValueDaily.ticker == ticker.upper(),
                    FairValueDaily.model == model,
                    FairValueDaily.scenario == scenario,
                )
                .order_by(FairValueDaily.date.desc(), FairValueDaily.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    return _fv_dict(row) if row else None


async def tickers_with_fair_values() -> list[str]:
    async with session_scope() as s:
        rows = (await s.execute(select(FairValueDaily.ticker).distinct())).scalars().all()
    return sorted(set(rows))


async def latest_runs(ticker: str, limit: int = 20) -> list[dict]:
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(ValuationRun)
                    .where(ValuationRun.ticker == ticker.upper())
                    .order_by(ValuationRun.id.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": r.id,
            "model_id": r.model_id,
            "model_version": r.model_version,
            "scenario": r.scenario,
            "assumption_set_id": r.assumption_set_id,
            "inputs_snapshot_id": r.inputs_snapshot_id,
            "value_per_share": r.value_per_share,
            "price": r.price,
            "price_source": r.price_source,
            "price_ts": r.price_ts,
            "upside": r.upside,
            "status": r.status,
            "created_at": r.created_at,
            "result": r.result,
        }
        for r in rows
    ]


async def overview(ticker: str) -> dict:
    """GET /api/valuation/{ticker}: latest runs, fair values by model/scenario, upside, football field, sensitivity."""
    from app.market.service import cached_quotes

    ticker = ticker.upper()
    runs = await latest_runs(ticker, 40)
    fvs = await latest_fair_values(ticker)
    q = (cached_quotes([ticker]) or [None])[0]
    price = q["last"] if q else (fvs[0]["price"] if fvs else None)
    latest_by_model: dict[str, dict] = {}
    for r in runs:
        if r["model_id"] not in latest_by_model and r["scenario"] == "base":
            latest_by_model[r["model_id"]] = r
    fcff = latest_by_model.get("fcff")
    grid = fcff["result"].get("sensitivity") if fcff else None
    scen = {}
    if fcff and fcff["result"].get("scenarios"):
        scen = fcff["result"]["scenarios"]
    ff = []
    if scen:
        ff.append(
            {
                "method": "DCF (bear/base/bull)",
                "low": scen.get("bear", scen.get("base")),
                "base": scen.get("base"),
                "high": scen.get("bull", scen.get("base")),
                "reference": "base_dcf",
            }
        )
    for mid, label, ref in (
        ("multiples", "Peer/history multiples", "multiples"),
        ("external", "External / imported", "imported_fv"),
    ):
        r = latest_by_model.get(mid)
        if r and r["value_per_share"] is not None:
            ff.append(
                {
                    "method": label,
                    "low": r["result"].get("low") or r["value_per_share"],
                    "base": r["value_per_share"],
                    "high": r["result"].get("high") or r["value_per_share"],
                    "reference": ref,
                }
            )
    reg = get_model_registry()
    for mid in reg.user_ids():
        r = latest_by_model.get(mid)
        if r and r["value_per_share"] is not None:
            ff.append(
                {
                    "method": f"User model: {mid}",
                    "low": r["result"].get("low") or r["value_per_share"],
                    "base": r["value_per_share"],
                    "high": r["result"].get("high") or r["value_per_share"],
                    "reference": f"model:{mid}",
                }
            )
    fv_map = {(f["model"], f["scenario"]): f for f in fvs}
    for model, label, ref in (("analyst", "Analyst targets", "analyst"), ("blended", "Blended", "blended")):
        f = fv_map.get((model, "base"))
        if f and f["value"] is not None:
            ff.append(
                {"method": label, "low": f["value"], "base": f["value"], "high": f["value"], "reference": ref}
            )
    ref_fv = (
        fv_map.get(("fcff", "base")) or fv_map.get(("blended", "base")) or fv_map.get(("external", "base"))
    )
    return {
        "ticker": ticker,
        "price": price,
        "price_source": q["source"] if q else (fvs[0]["price_source"] if fvs else None),
        "price_ts": q["ts"] if q else None,
        "quote_stale": q["stale"] if q else None,
        "reference": {
            "name": "base_dcf"
            if ref_fv and ref_fv["model"] == "fcff"
            else (ref_fv["model"] if ref_fv else None),
            "value": ref_fv["value"] if ref_fv else None,
            "upside": (ref_fv["value"] / price - 1) if ref_fv and ref_fv["value"] and price else None,
            "date": ref_fv["date"] if ref_fv else None,
        },
        "fair_values": fvs,
        "runs": [
            {k: v for k, v in r.items() if k != "result"}
            | {
                "warnings": r["result"].get("warnings", []),
                "low": r["result"].get("low"),
                "high": r["result"].get("high"),
                "implied_growth": r["result"].get("implied_growth"),
            }
            for r in runs
        ],
        "latest": {mid: r["result"] for mid, r in latest_by_model.items()},
        "football_field": ff,
        "sensitivity": grid,
        "scenarios": scen,
        "assumption_sets": await list_assumption_sets(ticker, 10),
    }


# --- multiples helpers -------------------------------------------------------------------


def _percentiles(vals: list[float]) -> dict[str, float] | None:
    v = [x for x in vals if x is not None and np.isfinite(x) and x > 0]
    if len(v) < 2:
        return None
    arr = np.array(v)
    return {
        "p25": float(np.percentile(arr, 25)),
        "median": float(np.median(arr)),
        "p75": float(np.percentile(arr, 75)),
        "n": len(v),
    }


def own_history_stats(inputs: InputsSnapshot) -> dict[str, dict[str, float]] | None:
    """Own-history multiples percentiles from annual statements + stored prices at each period end."""
    from app.market.service import read_ohlcv

    per_metric: dict[str, list[float]] = {}
    for p in inputs.annual:
        d = p.period_end
        df, _ = read_ohlcv(inputs.ticker, "1d", d - timedelta(days=14), d)
        if df.is_empty():
            continue
        price = float(df.tail(1)["close"][0])
        shares = p.get("shares_diluted_weighted") or p.get("shares_outstanding")
        if not shares:
            continue
        ebit = p.get("operating_income")
        m = {
            "revenue": p.get("revenue"),
            "ebitda": (ebit + (p.get("depreciation_amortization") or 0.0)) if ebit is not None else None,
            "eps": p.get("eps_diluted")
            or ((p.get("net_income") or 0.0) / shares if p.get("net_income") else None),
            "book": p.get("total_equity"),
            "shares": shares,
            "net_debt": (p.get("short_term_debt") or 0.0)
            + (p.get("long_term_debt") or 0.0)
            + (p.get("long_term_lease_liabilities") or 0.0)
            - (p.get("cash_and_equivalents") or 0.0)
            - (p.get("short_term_investments") or 0.0),
            "minority": p.get("minority_interest") or 0.0,
            "growth": None,
            "price": price,
        }
        for k, v in current_multiples(m).items():
            if v is not None:
                per_metric.setdefault(k, []).append(v)
    out = {k: st for k, vals in per_metric.items() if (st := _percentiles(vals))}
    return out or None


async def peer_stats_for(ticker: str) -> dict[str, dict[str, float]] | None:
    """Peer multiples percentiles from the stored peer set (needs the statements service for peers' TTM data)."""
    peers = await get_peers(ticker)
    if not peers or not peers["effective"]:
        return None
    try:
        from app.statements.service import get_statements  # type: ignore[import-not-found]
    except ImportError:
        return None
    from app.market.service import cached_quotes

    quotes = {q["ticker"]: q for q in cached_quotes(peers["effective"])}
    per_metric: dict[str, list[float]] = {}
    for t in peers["effective"]:
        q = quotes.get(t)
        if not q:
            continue
        try:
            data = await get_statements(t, period_type="TTM", restated=True, approved_only=True)
        except Exception:
            continue
        f = data.get("fields") or {}
        last = {k: (v[-1] if v else None) for k, v in f.items()}
        shares = last.get("shares_diluted_weighted") or last.get("shares_outstanding")
        if not shares:
            continue
        ebit = last.get("operating_income")
        m = {
            "revenue": last.get("revenue"),
            "ebitda": (ebit + (last.get("depreciation_amortization") or 0.0)) if ebit is not None else None,
            "eps": last.get("eps_diluted"),
            "book": last.get("total_equity"),
            "shares": shares,
            "net_debt": (last.get("short_term_debt") or 0.0)
            + (last.get("long_term_debt") or 0.0)
            - (last.get("cash_and_equivalents") or 0.0),
            "minority": last.get("minority_interest") or 0.0,
            "growth": None,
            "price": q["last"],
        }
        for k, v in current_multiples(m).items():
            if v is not None:
                per_metric.setdefault(k, []).append(v)
    out = {k: st for k, vals in per_metric.items() if (st := _percentiles(vals))}
    return out or None


# --- peers ----------------------------------------------------------------------------


async def get_peers(ticker: str) -> dict | None:
    async with session_scope() as s:
        row = await s.get(PeerSet, ticker.upper())
    if row is None:
        return None
    auto = [p["ticker"] for p in row.peers]
    effective = [
        t for t in dict.fromkeys(list(row.pins) + auto) if t not in row.excludes and t != ticker.upper()
    ]
    return {
        "ticker": row.ticker,
        "sic": row.sic,
        "industry": row.industry,
        "peers": row.peers,
        "pins": row.pins,
        "excludes": row.excludes,
        "effective": effective,
        "as_of": row.as_of,
        "updated_at": row.updated_at,
    }


async def set_peers(
    ticker: str, pins: list[str] | None = None, excludes: list[str] | None = None, industry: str | None = None
) -> dict:
    async with session_scope() as s:
        row = await s.get(PeerSet, ticker.upper())
        if row is None:
            row = PeerSet(ticker=ticker.upper(), peers=[], pins=[], excludes=[])
            s.add(row)
        if pins is not None:
            row.pins = [p.upper() for p in pins]
        if excludes is not None:
            row.excludes = [p.upper() for p in excludes]
        if industry is not None:
            row.industry = industry or None
    return await get_peers(ticker)  # type: ignore[return-value]


async def build_peers(ticker: str, max_peers: int = 12, cap_lo: float = 0.2, cap_hi: float = 5.0) -> dict:
    """Same-SIC (EDGAR submissions) ∩ Finnhub peers, market-cap filtered 0.2x-5x when caps are known; keeps user pins."""
    from app.market.service import cached_quotes

    ticker = ticker.upper()
    reg = get_registry()
    sic: str | None = None
    industry: str | None = None
    notes: list[str] = []
    edgar = None
    try:
        edgar = reg.get("edgar")
        if reg.is_usable(edgar)[0]:
            sub = await reg.call(edgar, "submissions", lambda: edgar.submissions(ticker), key=ticker)
            sic, industry = (str(sub.get("sic")) if sub.get("sic") else None), sub.get("sicDescription")
        else:
            edgar = None
    except Exception as e:
        notes.append(f"edgar: {e}"[:120])
        edgar = None
    candidates: dict[str, dict] = {}
    try:
        fh = reg.get("finnhub")
        if reg.is_usable(fh)[0]:
            peers = await reg.call(fh, "peers", lambda: fh.peers(ticker), key=ticker)
            for p in peers or []:
                if p and p.upper() != ticker:
                    candidates[p.upper()] = {
                        "ticker": p.upper(),
                        "source": "finnhub",
                        "sic": None,
                        "market_cap": None,
                    }
        else:
            notes.append("finnhub not configured: peers from pins only")
    except Exception as e:
        notes.append(f"finnhub: {e}"[:120])
    if edgar is not None and sic:
        for t, c in list(candidates.items())[: max_peers * 2]:
            try:
                sub = await reg.call(edgar, "submissions", lambda t=t: edgar.submissions(t), key=t)
                c["sic"] = str(sub.get("sic")) if sub.get("sic") else None
                c["same_sic"] = c["sic"] == sic
            except Exception:
                c["same_sic"] = None
    own_cap = None
    quotes = {q["ticker"]: q for q in cached_quotes([ticker, *candidates])}
    try:
        from app.statements.service import get_statements  # type: ignore[import-not-found]

        async def cap(t: str) -> float | None:
            q = quotes.get(t)
            if not q:
                return None
            data = await get_statements(t, period_type="TTM", restated=True, approved_only=True)
            sh = (data.get("fields") or {}).get("shares_outstanding") or []
            return q["last"] * sh[-1] if sh and sh[-1] else None

        own_cap = await cap(ticker)
        for t, c in candidates.items():
            try:
                c["market_cap"] = await cap(t)
            except Exception:
                c["market_cap"] = None
    except ImportError:
        notes.append("statements service missing: market-cap filter skipped")
    ranked = []
    for c in candidates.values():
        if own_cap and c.get("market_cap") and not (cap_lo * own_cap <= c["market_cap"] <= cap_hi * own_cap):
            continue
        score = (1 if c.get("same_sic") else 0, c.get("market_cap") or 0)
        ranked.append((score, c))
    ranked.sort(key=lambda x: x[0], reverse=True)
    peers = [c for _, c in ranked[:max_peers]]
    async with session_scope() as s:
        row = await s.get(PeerSet, ticker)
        if row is None:
            row = PeerSet(ticker=ticker, pins=[], excludes=[])
            s.add(row)
        row.sic, row.peers, row.as_of = sic, peers, _now()
        if industry and not row.industry:
            row.industry = industry
    out = await get_peers(ticker)
    assert out is not None
    out["notes"] = notes
    return out


# --- imports -----------------------------------------------------------------------------


async def import_assumptions(
    ticker: str,
    source: str | Path | bytes,
    kind: str,
    filename: str | None = None,
    cell_map: dict[str, str] | None = None,
) -> dict:
    """kind: csv | xlsx | sheets | ginzu. Creates one immutable assumption set per scenario found."""
    ticker = ticker.upper()
    created: list[dict] = []
    if kind == "ginzu":
        a, raw = GinzuImporter(cell_map).load(source)
        row = await create_assumption_set(
            ticker,
            a,
            name=f"ginzu {filename or ''}".strip(),
            scenario="base",
            source="import_ginzu",
            note=f"sheet {raw['sheet']}",
        )
        created.append(row)
        return {
            "kind": kind,
            "sets": created,
            "values": {
                k: (v if isinstance(v, int | float | str) or v is None else str(v))
                for k, v in raw["values"].items()
            },
            "labels": {k: (str(v) if v is not None else None) for k, v in raw["labels"].items()},
            "errors": [],
        }
    imp = await load_external(source, kind, filename)
    for scen, a in imp.assumptions.items():
        row = await create_assumption_set(
            ticker,
            a,
            name=f"{kind} {filename or imp.source or ''}"[:120].strip(),
            scenario=scen if scen in ("bear", "base", "bull") else "custom",
            source=f"import_{kind}",
            note=imp.source or None,
        )
        created.append(row)
    return {"kind": kind, "sets": created, "rows": imp.rows, "errors": imp.errors, "scenarios": imp.scenarios}
