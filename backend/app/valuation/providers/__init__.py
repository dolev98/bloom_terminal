"""Macro-input providers for valuation (Treasury rf, Damodaran ERP/CRP/industry) + the `val:` series provider.

`PROVIDERS` is exposed for the integrator to register in `app/data/registry.py::build_registry` so that
`val:<TICKER>:fair_value_base` catalog rows resolve (refresh is a no-op: the valuation service writes them).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import polars as pl

from app.data.providers.base import Capability, LicenseSpec, Provider, RateSpec, SeriesSpec


@dataclass
class ValuationSeriesProvider(Provider):
    id: str = "val"
    name: str = "Valuation outputs (fair value / upside)"
    capabilities: Capability = Capability.SERIES
    rate: RateSpec = field(default_factory=RateSpec)
    license: LicenseSpec = field(default_factory=lambda: LicenseSpec(grey=False, note="derived in-house"))

    async def describe(self, key: str) -> SeriesSpec:
        ticker, _, fld = key.partition(":")
        return SeriesSpec(
            series_id=f"val:{ticker}:{fld or 'fair_value_base'}",
            provider="val",
            provider_key=ticker,
            field_name=fld or "fair_value_base",
            name=f"{ticker} {fld or 'fair value'}",
            freq="1d",
            value_kind="price",
            category="valuation",
        )

    async def get_series(
        self, spec: SeriesSpec, since: date | None = None, vintage: date | None = None
    ) -> pl.DataFrame:
        from app.data.series_service import read_series

        return read_series(spec.series_id, since)


PROVIDERS = [ValuationSeriesProvider()]
