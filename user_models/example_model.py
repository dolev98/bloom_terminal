"""Example user model: Benjamin Graham's number, sqrt(22.5 x EPS x book value per share).

Copy this file, change `id`, `name` and `run`. Loaded automatically by the valuation registry.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from app.valuation.models.base import BaseValuationModel, ValuationResult


class GrahamAssumptions(BaseModel):
    """Shown as a form in the VAL panel (JSON schema)."""

    multiplier: float = Field(22.5, description="Graham's PE x PB product (15 x 1.5)")


class GrahamNumberModel(BaseValuationModel):
    id = "graham_number"
    name = "Graham number (example user model)"
    version = "1.0"
    inputs_required = {"eps_diluted", "total_equity"}
    assumptions_schema = GrahamAssumptions

    def run(self, inputs, a) -> ValuationResult:
        eps = inputs.value("eps_diluted")
        book = inputs.value("total_equity")
        shares = inputs.shares()
        mult = 22.5
        try:  # model-specific keys live under Assumptions.custom (the panel form writes custom.<key>)
            m = a.get_path("custom.multiplier") if hasattr(a, "get_path") else None
            mult = float(m) if m else 22.5
        except Exception:
            pass
        warnings = []
        if eps is None or book is None or not shares:
            return ValuationResult(
                model_id=self.id,
                model_version=self.version,
                warnings=["needs eps_diluted, total_equity and shares"],
            )
        bvps = book / shares
        if eps <= 0 or bvps <= 0:
            warnings.append("negative EPS or book value: Graham number undefined")
            value = None
        else:
            value = math.sqrt(mult * eps * bvps)
        return ValuationResult(
            model_id=self.id,
            model_version=self.version,
            value_per_share=value,
            equity_value=value * shares if value else None,
            components={"eps": eps, "bvps": bvps, "multiplier": mult},
            diagnostics={"formula": "sqrt(multiplier x EPS x BVPS)"},
            warnings=warnings,
        )


MODEL = GrahamNumberModel()
