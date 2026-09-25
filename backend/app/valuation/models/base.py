"""ValuationModel protocol + ValuationResult. Built-ins and user models (user_models/*.py) implement `run`."""

from __future__ import annotations

from typing import Any, ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.valuation.assumptions import Assumptions
from app.valuation.inputs import InputsSnapshot


class ValuationResult(BaseModel):
    model_id: str
    model_version: str = "1"
    scenario: str = "base"
    value_per_share: float | None = None
    ev: float | None = None
    equity_value: float | None = None
    low: float | None = None  # optional range (e.g. multiples p25 / external low)
    high: float | None = None
    implied_growth: float | None = None
    currency: str = "USD"
    components: dict[str, Any] = Field(
        default_factory=dict
    )  # EV bridge, per-year projections, per-metric values
    diagnostics: dict[str, Any] = Field(default_factory=dict)  # resolved assumptions, derivations, pv shares
    warnings: list[str] = Field(default_factory=list)

    def upside(self, price: float | None) -> float | None:
        if price and self.value_per_share is not None:
            return self.value_per_share / price - 1.0
        return None


@runtime_checkable
class ValuationModel(Protocol):
    """Duck-typed: anything with `id`, `name`, `version`, `inputs_required`, `assumptions_schema`, `run`."""

    id: str
    name: str
    version: str
    inputs_required: set[str]
    assumptions_schema: type[BaseModel]

    def run(self, inputs: InputsSnapshot, a: Assumptions) -> ValuationResult: ...


class BaseValuationModel:
    """Convenience base for built-ins: sensible class attributes + `check_inputs`."""

    id: ClassVar[str] = "base"
    name: ClassVar[str] = "Base"
    version: ClassVar[str] = "1"
    inputs_required: ClassVar[set[str]] = set()
    assumptions_schema: ClassVar[type[BaseModel]] = Assumptions
    reference: ClassVar[str | None] = (
        None  # alert reference name this model feeds (base_dcf, multiples, imported_fv)
    )

    def check_inputs(self, inputs: InputsSnapshot) -> list[str]:
        return inputs.missing(self.inputs_required)

    def run(self, inputs: InputsSnapshot, a: Assumptions) -> ValuationResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def describe(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "inputs_required": sorted(self.inputs_required),
            "reference": self.reference,
            "assumptions_schema": self.assumptions_schema.model_json_schema(by_alias=True),
        }
