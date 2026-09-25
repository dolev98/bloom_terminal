"""Extra series providers used by the macro module. The integrator registers `PROVIDERS` in app/data/registry.py::build_registry."""

from __future__ import annotations

from app.macro.providers.cbs import CbsProvider
from app.macro.providers.ons import OnsProvider
from app.macro.providers.sdmx_generic import (
    BisProvider,
    EcbProvider,
    EstatProvider,
    ImfProvider,
    OecdProvider,
)

PROVIDERS = [
    ImfProvider(),
    BisProvider(),
    EcbProvider(),
    EstatProvider(),
    OecdProvider(),
    CbsProvider(),
    OnsProvider(),
]

__all__ = [
    "PROVIDERS",
    "ImfProvider",
    "BisProvider",
    "EcbProvider",
    "EstatProvider",
    "OecdProvider",
    "CbsProvider",
    "OnsProvider",
]
