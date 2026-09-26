"""Import every ORM model module so `create_all` (and Alembic later) sees all tables. Packages append themselves here."""

from __future__ import annotations

import importlib
import logging

log = logging.getLogger(__name__)

MODEL_MODULES = [
    "app.data.store.models",
    "app.llm.models",
    # added by packages (integrator keeps this list):
    "app.statements.models",
    "app.correlation.models",
    "app.valuation.models",
    "app.alerts.models",
    "app.calendar.models",
    "app.macro.models",
    "app.news.models",
]


def import_all() -> list[str]:
    loaded = []
    for mod in MODEL_MODULES:
        try:
            importlib.import_module(mod)
            loaded.append(mod)
        except ModuleNotFoundError as e:
            if e.name and (mod.startswith(e.name) or e.name.startswith(mod.rsplit(".", 1)[0])):
                continue  # package not built yet
            raise
    return loaded
