"""Formula (derived) series: arithmetic over other catalog series, aligned on ts with forward-fill."""

from __future__ import annotations

import re

import polars as pl

from app.data.providers.base import empty_observations

_ID_RE = re.compile(
    r"[a-z][a-z0-9_]*:[A-Za-z^][A-Za-z0-9_.^=\-]*(?:/[A-Za-z][A-Za-z0-9_.^=\-]*)*(?::[A-Za-z][A-Za-z0-9_]*)?"
)
_SAFE_RE = re.compile(r"^[\sA-Za-z0-9_+\-*/().,]+$")


def formula_inputs(formula: str) -> list[str]:
    seen: list[str] = []
    for m in _ID_RE.finditer(formula):
        if m.group(0) not in seen:
            seen.append(m.group(0))
    return seen


def evaluate(formula: str, frames: dict[str, pl.DataFrame], ffill_limit: int = 10) -> pl.DataFrame:
    ids = formula_inputs(formula)
    if not ids or any(fid not in frames or frames[fid].is_empty() for fid in ids):
        return empty_observations()
    expr = formula
    names: dict[str, str] = {}
    for i, fid in enumerate(sorted(ids, key=len, reverse=True)):
        var = f"s{i}"
        names[fid] = var
        expr = expr.replace(fid, var)
    if not _SAFE_RE.match(expr):
        raise ValueError(f"unsafe characters in formula {formula!r}")
    wide: pl.DataFrame | None = None
    for fid, var in names.items():
        f = frames[fid].select(["ts", "value"]).rename({"value": var})
        wide = f if wide is None else wide.join(f, on="ts", how="full", coalesce=True)
    assert wide is not None
    wide = wide.sort("ts")
    if ffill_limit:
        wide = wide.with_columns(
            [pl.col(v).fill_null(strategy="forward", limit=ffill_limit) for v in names.values()]
        )
    env = {v: pl.col(v) for v in names.values()}
    result = eval(expr, {"__builtins__": {}}, env)  # noqa: S307 - validated by _SAFE_RE
    out = wide.select(pl.col("ts"), result.alias("value")).drop_nulls("value")
    return out.with_columns(pl.col("value").cast(pl.Float64))
