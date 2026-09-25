"""2-D sensitivity grids: value per share over (WACC x terminal g) and (growth x target margin)."""

from __future__ import annotations

from collections.abc import Sequence

from app.valuation.assumptions import Assumptions
from app.valuation.inputs import InputsSnapshot


def grid(
    model,
    inputs: InputsSnapshot,
    a: Assumptions,
    x_path: str,
    x_values: Sequence[float],
    y_path: str,
    y_values: Sequence[float],
    policies=None,
) -> dict:
    """Generic grid: rows = y values, cols = x values. Any Assumptions dotted path works."""
    values: list[list[float | None]] = []
    for y in y_values:
        row: list[float | None] = []
        for x in x_values:
            try:
                res = (
                    model.run(inputs, a.with_path(x_path, x).with_path(y_path, y), policies)
                    if _accepts_policies(model)
                    else model.run(inputs, a.with_path(x_path, x).with_path(y_path, y))
                )
                row.append(res.value_per_share)
            except Exception:
                row.append(None)
        values.append(row)
    return {
        "x": {"path": x_path, "values": list(x_values)},
        "y": {"path": y_path, "values": list(y_values)},
        "values": values,
    }


def wacc_g_grid(
    model,
    inputs: InputsSnapshot,
    a: Assumptions,
    policies=None,
    steps: int = 5,
    wacc_step: float = 0.01,
    g_step: float = 0.005,
) -> dict:
    r = a.resolve(inputs, policies)
    w0, g0 = r.wacc, r.terminal_g
    k = steps // 2
    waccs = [round(w0 + (i - k) * wacc_step, 4) for i in range(steps)]
    gs = [round(g0 + (i - k) * g_step, 4) for i in range(steps)]
    gs = [g for g in gs if g < min(waccs)]  # keep the grid stable
    return {
        **grid(model, inputs, a, "wacc.wacc", waccs, "terminal.g", gs, policies),
        "center": {"wacc": w0, "g": g0},
    }


def growth_margin_grid(
    model,
    inputs: InputsSnapshot,
    a: Assumptions,
    policies=None,
    steps: int = 5,
    g_step: float = 0.02,
    m_step: float = 0.02,
) -> dict:
    r = a.resolve(inputs, policies)
    g0 = a.revenue_growth if a.revenue_growth is not None else r.growth_path[0]
    m0 = a.target_ebit_margin if a.target_ebit_margin is not None else r.margin_path[-1]
    k = steps // 2
    gs = [round(g0 + (i - k) * g_step, 4) for i in range(steps)]
    ms = [round(m0 + (i - k) * m_step, 4) for i in range(steps)]
    a0 = a.model_copy(deep=True)
    a0.revenue_growth_path = None
    return {
        **grid(model, inputs, a0, "revenue_growth", gs, "target_ebit_margin", ms, policies),
        "center": {"growth": g0, "margin": m0},
    }


def _accepts_policies(model) -> bool:
    import inspect

    try:
        return "policies" in inspect.signature(model.run).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return False
