"""Reverse DCF: solve (scipy brentq) for the year-1 revenue growth that makes the FCFF value equal the market price.

Also reports the implied terminal ROIC and the implied margin (solved separately, growth held) for context.
"""

from __future__ import annotations

from typing import ClassVar

from scipy.optimize import brentq

from app.valuation.assumptions import Assumptions
from app.valuation.inputs import InputsSnapshot
from app.valuation.models.base import BaseValuationModel, ValuationResult
from app.valuation.models.fcff import FCFFModel


class ReverseDCFModel(BaseValuationModel):
    id: ClassVar[str] = "reverse_dcf"
    name: ClassVar[str] = "Reverse DCF (implied growth)"
    version: ClassVar[str] = "1.0"
    inputs_required: ClassVar[set[str]] = {"revenue", "operating_income"}
    reference: ClassVar[str | None] = None

    def run(self, inputs: InputsSnapshot, a: Assumptions, policies=None) -> ValuationResult:
        price = inputs.market.price
        base = FCFFModel()
        if not price:
            return ValuationResult(
                model_id=self.id,
                model_version=self.version,
                warnings=["no price: cannot solve"],
                currency=inputs.currency,
            )
        # growth is solved: drop explicit paths, keep the fade
        a0 = a.model_copy(deep=True)
        a0.revenue_growth_path = None

        def vps(g: float) -> float | None:
            res = base.run(inputs, a0.with_path("revenue_growth", g), policies)
            return res.value_per_share

        def f(g: float) -> float:
            v = vps(g)
            return (v if v is not None else 0.0) - price

        lo, hi = -0.5, 1.5
        try:
            flo, fhi = f(lo), f(hi)
            if flo * fhi > 0:
                return ValuationResult(
                    model_id=self.id,
                    model_version=self.version,
                    currency=inputs.currency,
                    warnings=[
                        f"no growth in [{lo}, {hi}] reproduces the price (f(lo)={flo:.2f}, f(hi)={fhi:.2f})"
                    ],
                )
            g_star = float(brentq(f, lo, hi, xtol=1e-6, maxiter=200))
        except Exception as e:
            return ValuationResult(
                model_id=self.id,
                model_version=self.version,
                currency=inputs.currency,
                warnings=[f"solver failed: {e}"],
            )
        res = base.run(inputs, a0.with_path("revenue_growth", g_star), policies)
        proj = res.components.get("projection", {})
        # implied margin: hold the *default* growth and solve for target margin instead
        implied_margin = None
        try:
            a1 = a.model_copy(deep=True)

            def fm(m: float) -> float:
                v = base.run(inputs, a1.with_path("target_ebit_margin", m), policies).value_per_share
                return (v if v is not None else 0.0) - price

            if fm(-0.5) * fm(0.8) < 0:
                implied_margin = float(brentq(fm, -0.5, 0.8, xtol=1e-6, maxiter=200))
        except Exception:  # pragma: no cover - informational only
            implied_margin = None
        return ValuationResult(
            model_id=self.id,
            model_version=self.version,
            scenario=res.scenario,
            value_per_share=res.value_per_share,
            ev=res.ev,
            equity_value=res.equity_value,
            implied_growth=g_star,
            currency=inputs.currency,
            components={
                "implied_growth": g_star,
                "implied_target_margin": implied_margin,
                "growth_path": [y["growth"] for y in proj.get("years", [])],
                "bridge": res.components.get("bridge"),
            },
            diagnostics={
                "price": price,
                "implied_terminal_roic": proj.get("implied_terminal_roic"),
                "terminal_reinvestment_rate": proj.get("terminal_reinvestment_rate"),
                "wacc": res.diagnostics.get("wacc"),
                "terminal_g": res.diagnostics.get("terminal_g"),
            },
            warnings=res.warnings,
        )


MODEL = ReverseDCFModel()
