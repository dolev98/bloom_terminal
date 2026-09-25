"""Bear / base / bull: run one model over three assumption sets (derived from base when not supplied)."""

from __future__ import annotations

from app.valuation.assumptions import Assumptions
from app.valuation.inputs import InputsSnapshot
from app.valuation.models.base import ValuationResult


def derive_scenarios(
    base: Assumptions,
    inputs: InputsSnapshot,
    policies=None,
    growth_shift: float = 0.02,
    margin_shift: float = 0.02,
    wacc_shift: float = 0.01,
) -> dict[str, Assumptions]:
    """Bear/bull from base: growth -/+ shift, target margin -/+ shift, WACC +/- shift, terminal g -/+ 0.5pp (bounded by rf)."""
    r = base.resolve(inputs, policies)
    out: dict[str, Assumptions] = {"base": base.model_copy(deep=True)}
    out["base"].scenario = "base"
    for name, sign in (("bear", -1), ("bull", 1)):
        v = base.model_copy(deep=True)
        v.scenario = name  # type: ignore[assignment]
        if base.revenue_growth_path:
            v.revenue_growth_path = [g + sign * growth_shift for g in base.revenue_growth_path]
        else:
            v.revenue_growth = r.growth_path[0] + sign * growth_shift
        v.target_ebit_margin = r.margin_path[-1] + sign * margin_shift
        v.wacc.wacc = max(r.wacc - sign * wacc_shift, 0.02)
        v.terminal.g = min(r.terminal_g + sign * 0.005, r.rf)
        out[name] = v
    return out


def run_scenarios(
    model, inputs: InputsSnapshot, sets: dict[str, Assumptions], policies=None
) -> dict[str, ValuationResult]:
    import inspect

    accepts = "policies" in inspect.signature(model.run).parameters
    out: dict[str, ValuationResult] = {}
    for name, a in sets.items():
        try:
            res = model.run(inputs, a, policies) if accepts else model.run(inputs, a)
        except Exception as e:
            res = ValuationResult(
                model_id=getattr(model, "id", "?"), scenario=name, warnings=[f"{type(e).__name__}: {e}"[:200]]
            )
        res.scenario = name
        out[name] = res
    return out
