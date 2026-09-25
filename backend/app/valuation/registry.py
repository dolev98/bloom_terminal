"""Model registry: built-ins + auto-discovery of `user_models/*.py` at the repo root (hot-reloadable).

A user module contributes either a `MODEL = SomeModel()` instance or any class with `run` and `id` attributes
(instantiated with no arguments). See user_models/README.md.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT
from app.valuation.models.external import ExternalModel
from app.valuation.models.fcff import FCFFModel
from app.valuation.models.multiples import MultiplesModel
from app.valuation.models.reverse_dcf import ReverseDCFModel

log = logging.getLogger(__name__)
USER_MODELS_DIR = PROJECT_ROOT / "user_models"

# alert reference name -> model id (rules reference a *named value*, not a model; swapping models keeps rules valid)
REFERENCE_MODELS: dict[str, str] = {
    "base_dcf": "fcff",
    "imported_fv": "external",
    "multiples": "multiples",
    "blended": "blended",
    "analyst": "analyst",
    "reverse_dcf": "reverse_dcf",
}


def reference_to_model(reference: str) -> str:
    if reference.startswith("model:"):
        return reference.split(":", 1)[1]
    return REFERENCE_MODELS.get(reference, reference)


class ModelRegistry:
    def __init__(self, user_dir: Path | None = None):
        self.user_dir = user_dir or USER_MODELS_DIR
        self._builtin: dict[str, Any] = {}
        self._user: dict[str, Any] = {}
        self.errors: dict[str, str] = {}
        self.loaded_at: datetime | None = None
        for m in (FCFFModel(), ReverseDCFModel(), MultiplesModel(), ExternalModel()):
            self._builtin[m.id] = m

    # --- access ------------------------------------------------------------
    def get(self, model_id: str) -> Any:
        m = self._builtin.get(model_id) or self._user.get(model_id)
        if m is None:
            raise KeyError(f"unknown valuation model {model_id!r}")
        return m

    def has(self, model_id: str) -> bool:
        return model_id in self._builtin or model_id in self._user

    def ids(self) -> list[str]:
        return list(self._builtin) + list(self._user)

    def builtin_ids(self) -> list[str]:
        return list(self._builtin)

    def user_ids(self) -> list[str]:
        return list(self._user)

    def describe(self) -> list[dict]:
        out = []
        for mid in self.ids():
            m = self.get(mid)
            out.append({**_describe(m), "user": mid in self._user})
        return out

    # --- discovery ---------------------------------------------------------
    def reload_user_models(self) -> dict:
        self._user.clear()
        self.errors.clear()
        if self.user_dir.exists():
            for path in sorted(self.user_dir.glob("*.py")):
                if path.name.startswith("_"):
                    continue
                try:
                    for m in _load_module_models(path):
                        mid = str(m.id)
                        if mid in self._builtin:
                            self.errors[path.name] = f"id {mid!r} clashes with a built-in; skipped"
                            continue
                        self._user[mid] = m
                except Exception as e:
                    self.errors[path.name] = f"{type(e).__name__}: {e}"[:300]
                    log.warning("user model %s failed to load: %s", path.name, e)
        self.loaded_at = datetime.now(tz=UTC).replace(tzinfo=None)
        return {"loaded": self.user_ids(), "errors": dict(self.errors), "dir": str(self.user_dir)}


def _load_module_models(path: Path) -> list[Any]:
    mod_name = f"user_models_{path.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop(mod_name, None)
    spec.loader.exec_module(module)
    found: list[Any] = []
    explicit = getattr(module, "MODEL", None)
    if explicit is not None and _is_model(explicit):
        found.append(explicit)
    for name, obj in vars(module).items():
        if name.startswith("_") or not isinstance(obj, type):
            continue
        if obj.__module__ != module.__name__:
            continue
        if callable(getattr(obj, "run", None)) and getattr(obj, "id", None):
            try:
                inst = obj()
            except Exception as e:
                raise RuntimeError(f"{name}: cannot instantiate without arguments ({e})") from e
            if all(getattr(inst, "id", None) != getattr(f, "id", None) for f in found):
                found.append(inst)
    if not found:
        raise RuntimeError("no MODEL instance or class with `run` and `id` found")
    return found


def _is_model(obj: Any) -> bool:
    return callable(getattr(obj, "run", None)) and bool(getattr(obj, "id", None))


def _describe(m: Any) -> dict:
    if hasattr(m, "describe"):
        try:
            return m.describe()
        except Exception:  # pragma: no cover - user code
            pass
    schema_cls = getattr(m, "assumptions_schema", None)
    schema: dict = {}
    if schema_cls is not None and hasattr(schema_cls, "model_json_schema"):
        try:
            schema = schema_cls.model_json_schema(by_alias=True)
        except Exception:  # pragma: no cover
            schema = {}
    return {
        "id": getattr(m, "id", "?"),
        "name": getattr(m, "name", getattr(m, "id", "?")),
        "version": str(getattr(m, "version", "")),
        "inputs_required": sorted(getattr(m, "inputs_required", set()) or []),
        "reference": getattr(m, "reference", None),
        "assumptions_schema": schema,
    }


_registry: ModelRegistry | None = None


def get_model_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
        _registry.reload_user_models()
    return _registry


def set_model_registry(reg: ModelRegistry | None) -> None:
    global _registry
    _registry = reg
