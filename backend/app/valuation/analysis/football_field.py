"""Football-field rows: {method, low, base, high} from DCF scenarios, multiples, own history, 52-week range, analyst targets."""

from __future__ import annotations

from app.valuation.inputs import InputsSnapshot
from app.valuation.models.base import ValuationResult


def rows(
    inputs: InputsSnapshot,
    scenarios: dict[str, ValuationResult] | None = None,
    multiples: ValuationResult | None = None,
    history: ValuationResult | None = None,
    external: ValuationResult | None = None,
    user: dict[str, ValuationResult] | None = None,
) -> list[dict]:
    out: list[dict] = []
    if scenarios:
        vals = {k: v.value_per_share for k, v in scenarios.items() if v.value_per_share is not None}
        if vals:
            base = vals.get("base")
            out.append(
                {
                    "method": "DCF (bear/base/bull)",
                    "low": vals.get("bear", base),
                    "base": base,
                    "high": vals.get("bull", base),
                    "reference": "base_dcf",
                }
            )
    if multiples and multiples.value_per_share is not None:
        out.append(
            {
                "method": "Peer multiples",
                "low": multiples.low,
                "base": multiples.value_per_share,
                "high": multiples.high,
                "reference": "multiples",
            }
        )
    if history and history.value_per_share is not None:
        out.append(
            {
                "method": "Own-history multiples",
                "low": history.low,
                "base": history.value_per_share,
                "high": history.high,
                "reference": "history",
            }
        )
    if external and external.value_per_share is not None:
        out.append(
            {
                "method": "External / imported",
                "low": external.low,
                "base": external.value_per_share,
                "high": external.high,
                "reference": "imported_fv",
            }
        )
    for mid, res in (user or {}).items():
        if res.value_per_share is not None:
            out.append(
                {
                    "method": f"User model: {mid}",
                    "low": res.low,
                    "base": res.value_per_share,
                    "high": res.high,
                    "reference": f"model:{mid}",
                }
            )
    if inputs.market.low_52w and inputs.market.high_52w:
        out.append(
            {
                "method": "52-week range",
                "low": inputs.market.low_52w,
                "base": inputs.market.price,
                "high": inputs.market.high_52w,
                "reference": "price",
            }
        )
    c = inputs.consensus
    if c and c.target_mean:
        out.append(
            {
                "method": "Analyst targets",
                "low": c.target_low,
                "base": c.target_mean,
                "high": c.target_high,
                "reference": "analyst",
            }
        )
    for r in out:
        for k in ("low", "high"):
            if r.get(k) is None:
                r[k] = r["base"]
    return out
