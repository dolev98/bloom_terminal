"""Metric functions per rule type + the schema the UI form is generated from (`GET /api/alerts/rule-types`).

A metric returns `Metric(value, ...)`; the engine compares it with the rule threshold according to `direction`
('above' fires when value >= threshold, 'below' when value <= threshold, 'cross' when the sign changes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RULE_SCHEMA: dict[str, dict[str, Any]] = {
    "upside_gt": {
        "label": "Upside vs fair value > X%",
        "direction": "above",
        "unit": "%",
        "needs": ["reference"],
        "default_threshold": 25.0,
        "help": "(fair value / price - 1) x 100 above threshold",
    },
    "price_below_fv": {
        "label": "Price below fair value by X%",
        "direction": "above",
        "unit": "%",
        "needs": ["reference"],
        "default_threshold": 0.0,
        "help": "(1 - price / fair value) x 100 above threshold",
    },
    "mos_gt": {
        "label": "Margin of safety > X%",
        "direction": "above",
        "unit": "%",
        "needs": ["reference"],
        "default_threshold": 30.0,
        "help": "same as price_below_fv; conventionally on `blended`",
    },
    "price_crosses_fv": {
        "label": "Price crosses fair value",
        "direction": "cross",
        "unit": "%",
        "needs": ["reference"],
        "default_threshold": 0.0,
        "help": "fires when (price / fair value - 1) changes sign",
    },
    "consensus_gap_gt": {
        "label": "Analyst target vs price > X%",
        "direction": "above",
        "unit": "%",
        "needs": [],
        "default_threshold": 20.0,
        "help": "(analyst target mean / price - 1) x 100",
    },
    "implied_growth_lt": {
        "label": "Implied growth (reverse DCF) < X%",
        "direction": "below",
        "unit": "%",
        "needs": [],
        "default_threshold": 5.0,
        "help": "market-implied year-1 revenue growth below threshold",
    },
    "price_above": {
        "label": "Price above X",
        "direction": "above",
        "unit": "price",
        "needs": [],
        "default_threshold": 0.0,
    },
    "price_below": {
        "label": "Price below X",
        "direction": "below",
        "unit": "price",
        "needs": [],
        "default_threshold": 0.0,
    },
    "pct_change_gt": {
        "label": "Daily move > X% (abs)",
        "direction": "above",
        "unit": "%",
        "needs": [],
        "default_threshold": 5.0,
    },
    "series_threshold": {
        "label": "Series last value vs X",
        "direction": "above",
        "unit": "level",
        "needs": ["series_id"],
        "default_threshold": 0.0,
        "help": "params.direction = above|below (default above)",
    },
}


@dataclass
class Metric:
    ok: bool
    value: float | None = None
    reason: str | None = None
    price: float | None = None
    price_source: str | None = None
    price_ts: Any = None
    fair_value: float | None = None
    extra: dict = field(default_factory=dict)


def direction_for(rule_type: str, params: dict | None = None) -> str:
    if rule_type == "series_threshold" and params and params.get("direction") in ("above", "below"):
        return params["direction"]
    return RULE_SCHEMA.get(rule_type, {}).get("direction", "above")


def _q(quote: dict | None) -> tuple[float | None, str | None, Any]:
    if not quote:
        return None, None, None
    return quote.get("last"), quote.get("source"), quote.get("ts")


def compute_metric(
    rule_type: str,
    quote: dict | None,
    fv: dict | None,
    series_last: float | None = None,
    analyst: dict | None = None,
    reverse: dict | None = None,
) -> Metric:
    """Pure: quote = cached_quotes() item, fv = fair_value_daily row dict, analyst/reverse = reference rows."""
    price, src, ts = _q(quote)
    if rule_type == "series_threshold":
        if series_last is None:
            return Metric(False, reason="series has no data")
        return Metric(True, value=series_last, price=price, price_source=src, price_ts=ts)
    if price is None:
        return Metric(False, reason="no quote")
    if rule_type == "price_above" or rule_type == "price_below":
        return Metric(True, value=price, price=price, price_source=src, price_ts=ts)
    if rule_type == "pct_change_gt":
        chg = quote.get("change_pct") if quote else None
        if chg is None:
            return Metric(False, reason="no change_pct on quote", price=price, price_source=src, price_ts=ts)
        return Metric(
            True, value=abs(float(chg)), price=price, price_source=src, price_ts=ts, extra={"change_pct": chg}
        )
    if rule_type == "consensus_gap_gt":
        tgt = analyst.get("value") if analyst else None
        if not tgt:
            return Metric(False, reason="no analyst target", price=price, price_source=src, price_ts=ts)
        return Metric(
            True, value=(tgt / price - 1) * 100, price=price, price_source=src, price_ts=ts, fair_value=tgt
        )
    if rule_type == "implied_growth_lt":
        g = reverse.get("implied_growth") if reverse else None
        if g is None:
            return Metric(False, reason="no reverse-DCF run", price=price, price_source=src, price_ts=ts)
        return Metric(
            True,
            value=g * 100,
            price=price,
            price_source=src,
            price_ts=ts,
            fair_value=reverse.get("value") if reverse else None,
        )
    value_fv = fv.get("value") if fv else None
    if not value_fv:
        return Metric(False, reason="no fair value for reference", price=price, price_source=src, price_ts=ts)
    if rule_type == "upside_gt":
        return Metric(
            True,
            value=(value_fv / price - 1) * 100,
            price=price,
            price_source=src,
            price_ts=ts,
            fair_value=value_fv,
        )
    if rule_type in ("price_below_fv", "mos_gt"):
        return Metric(
            True,
            value=(1 - price / value_fv) * 100,
            price=price,
            price_source=src,
            price_ts=ts,
            fair_value=value_fv,
        )
    if rule_type == "price_crosses_fv":
        return Metric(
            True,
            value=(price / value_fv - 1) * 100,
            price=price,
            price_source=src,
            price_ts=ts,
            fair_value=value_fv,
        )
    return Metric(False, reason=f"unknown rule type {rule_type}")


def crossed(direction: str, value: float, threshold: float, last_value: float | None) -> bool:
    if direction == "above":
        return value >= threshold
    if direction == "below":
        return value <= threshold
    if direction == "cross":
        if last_value is None:
            return False
        return (value - threshold) * (last_value - threshold) < 0
    return False


def rearmed(direction: str, value: float, threshold: float, hysteresis: float) -> bool:
    if direction == "above":
        return value < threshold - hysteresis
    if direction == "below":
        return value > threshold + hysteresis
    if direction == "cross":
        return abs(value - threshold) >= hysteresis
    return False


def format_message(rule: Any, ticker: str, m: Metric, reference: str | None) -> str:
    """Telegram HTML: ticker, metric, threshold, price + source, fair value, link text."""
    unit = RULE_SCHEMA.get(rule.rule_type, {}).get("unit", "")
    val = f"{m.value:,.2f}{'%' if unit == '%' else ''}" if m.value is not None else "—"
    thr = f"{rule.threshold:,.2f}{'%' if unit == '%' else ''}"
    price = f"{m.price:,.2f}" if m.price is not None else "—"
    lines = [
        f"<b>{_esc(ticker)}</b> · {_esc(RULE_SCHEMA.get(rule.rule_type, {}).get('label', rule.rule_type))}",
        f"metric <b>{val}</b> vs threshold {thr}",
        f"price {price} ({_esc(m.price_source or '?')}{', ' + _fmt_ts(m.price_ts) if m.price_ts else ''})",
    ]
    if m.fair_value is not None:
        lines.append(f"fair value {m.fair_value:,.2f} [{_esc(reference or '')}]")
    if rule.name:
        lines.append(f"rule: {_esc(rule.name)}")
    lines.append(f"open: VAL {_esc(ticker)} · ALRT")
    return "\n".join(lines)


def _fmt_ts(ts: Any) -> str:
    s = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    return s.replace("T", " ")[:16]


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
