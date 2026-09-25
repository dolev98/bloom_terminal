"""Blended fair value: weighted median of the available references (FCFF 40 / multiples 25 / analyst 10 / external 25)."""

from __future__ import annotations

DEFAULT_WEIGHTS = {"base_dcf": 40.0, "multiples": 25.0, "analyst": 10.0, "imported_fv": 25.0}


def weighted_median(
    values: dict[str, float | None], weights: dict[str, float] | None = None
) -> tuple[float | None, dict[str, float]]:
    """Returns (blended value, weights actually used (renormalised over present references))."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    items = [(k, v, w.get(k, 0.0)) for k, v in values.items() if v is not None and w.get(k, 0.0) > 0]
    if not items:
        return None, {}
    total = sum(x[2] for x in items)
    used = {k: wt / total for k, _, wt in items}
    items.sort(key=lambda x: x[1])
    acc = 0.0
    for _k, v, wt in items:
        acc += wt / total
        if acc >= 0.5:
            return v, used
    return items[-1][1], used


def blend(results: dict[str, float | None], weights: dict[str, float] | None = None) -> dict:
    v, used = weighted_median(results, weights)
    return {"value": v, "weights": used, "inputs": {k: x for k, x in results.items() if x is not None}}
