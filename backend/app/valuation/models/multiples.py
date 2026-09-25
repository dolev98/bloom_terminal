"""Relative valuation: EV/EBITDA, EV/Sales, P/E, P/S, P/B, PEG vs peer stats and/or own-history percentiles.

Peer stats come in through `Assumptions.peer_stats` ({metric: {p25, median, p75}}); the service fills
`history_stats` from stored statements + prices. Implied price per metric goes through the EV -> equity bridge.
"""

from __future__ import annotations

from statistics import median
from typing import ClassVar

from app.valuation.assumptions import Assumptions
from app.valuation.inputs import InputsSnapshot
from app.valuation.models.base import BaseValuationModel, ValuationResult

METRICS = ("ev_ebitda", "ev_sales", "pe", "ps", "pb", "peg")
EV_METRICS = {"ev_ebitda", "ev_sales"}


def company_metrics(inputs: InputsSnapshot) -> dict[str, float | None]:
    """Per-share / aggregate figures the multiples are applied to (TTM first)."""
    rev = inputs.value("revenue")
    ebit = inputs.value("operating_income")
    da = inputs.value("depreciation_amortization") or 0.0
    shares = inputs.shares()
    ni = inputs.value("net_income")
    eps = inputs.value("eps_diluted")
    if eps is None and ni is not None and shares:
        eps = ni / shares
    book = inputs.value("total_equity")
    net_debt = (
        inputs.total_debt() + (inputs.value("long_term_lease_liabilities") or 0.0) - inputs.cash_and_sti()
    )
    growth = None
    if inputs.consensus and inputs.consensus.revenue_growth_5y is not None:
        growth = inputs.consensus.revenue_growth_5y
    else:
        eps_hist = [x for x in inputs.series("eps_diluted") if x is not None and x > 0]
        if len(eps_hist) >= 3:
            n = min(len(eps_hist) - 1, 5)
            growth = (eps_hist[-1] / eps_hist[-1 - n]) ** (1 / n) - 1
    return {
        "revenue": rev,
        "ebitda": (ebit + da) if ebit is not None else None,
        "eps": eps,
        "book": book,
        "shares": shares,
        "net_debt": net_debt,
        "minority": inputs.value("minority_interest") or 0.0,
        "growth": growth,
        "price": inputs.market.price,
    }


def current_multiples(m: dict[str, float | None]) -> dict[str, float | None]:
    price, sh = m.get("price"), m.get("shares")
    if not price or not sh:
        return {k: None for k in METRICS}
    mcap = price * sh
    ev = mcap + (m["net_debt"] or 0.0) + (m["minority"] or 0.0)
    out: dict[str, float | None] = {}
    out["ev_ebitda"] = ev / m["ebitda"] if m.get("ebitda") else None
    out["ev_sales"] = ev / m["revenue"] if m.get("revenue") else None
    out["pe"] = price / m["eps"] if m.get("eps") and m["eps"] > 0 else None
    out["ps"] = mcap / m["revenue"] if m.get("revenue") else None
    out["pb"] = mcap / m["book"] if m.get("book") and m["book"] > 0 else None
    g = m.get("growth")
    out["peg"] = (out["pe"] / (g * 100)) if out["pe"] and g and g > 0 else None
    return out


def implied_price(metric: str, multiple: float, m: dict[str, float | None]) -> float | None:
    sh = m.get("shares")
    if not sh or multiple is None:
        return None
    if metric == "ev_ebitda":
        if not m.get("ebitda") or m["ebitda"] <= 0:
            return None
        ev = multiple * m["ebitda"]
    elif metric == "ev_sales":
        if not m.get("revenue"):
            return None
        ev = multiple * m["revenue"]
    elif metric == "pe":
        return multiple * m["eps"] if m.get("eps") and m["eps"] > 0 else None
    elif metric == "ps":
        return multiple * m["revenue"] / sh if m.get("revenue") else None
    elif metric == "pb":
        return multiple * m["book"] / sh if m.get("book") and m["book"] > 0 else None
    elif metric == "peg":
        g = m.get("growth")
        return multiple * (g * 100) * m["eps"] if g and g > 0 and m.get("eps") and m["eps"] > 0 else None
    else:
        return None
    equity = ev - (m["net_debt"] or 0.0) - (m["minority"] or 0.0)
    return equity / sh


class MultiplesModel(BaseValuationModel):
    id: ClassVar[str] = "multiples"
    name: ClassVar[str] = "Relative valuation (multiples)"
    version: ClassVar[str] = "1.0"
    inputs_required: ClassVar[set[str]] = {"revenue"}
    reference: ClassVar[str | None] = "multiples"

    def run(self, inputs: InputsSnapshot, a: Assumptions, policies=None) -> ValuationResult:
        m = company_metrics(inputs)
        cur = current_multiples(m)
        use = a.multiples_use or "both"
        stats: dict[str, dict[str, float]] = {}
        if use in ("peers", "both") and a.peer_stats:
            stats.update({k: v for k, v in a.peer_stats.items() if k in METRICS})
        if use in ("history", "both") and a.history_stats:
            for k, v in a.history_stats.items():
                if k in METRICS and k not in stats:
                    stats[k] = v
                elif k in METRICS and use == "both":
                    # blend peers and history: simple average of each percentile
                    stats[k] = {
                        q: (stats[k][q] + v[q]) / 2
                        for q in ("p25", "median", "p75")
                        if q in stats[k] and q in v
                    }
        warnings: list[str] = []
        if not stats:
            warnings.append("no peer or history multiples supplied")
        comps: dict[str, dict] = {}
        lows, bases, highs = [], [], []
        for metric, st in stats.items():
            row = {"multiple": st, "current": cur.get(metric), "implied": {}}
            for q, key in (("p25", "low"), ("median", "base"), ("p75", "high")):
                if q in st:
                    row["implied"][key] = implied_price(metric, st[q], m)
            comps[metric] = row
            if row["implied"].get("base") is not None:
                bases.append(row["implied"]["base"])
                if row["implied"].get("low") is not None:
                    lows.append(row["implied"]["low"])
                if row["implied"].get("high") is not None:
                    highs.append(row["implied"]["high"])
        vps = median(bases) if bases else None
        price = m.get("price")
        equity = vps * m["shares"] if vps is not None and m.get("shares") else None
        return ValuationResult(
            model_id=self.id,
            model_version=self.version,
            scenario=a.scenario or "base",
            value_per_share=vps,
            equity_value=equity,
            ev=(equity + (m["net_debt"] or 0.0) + (m["minority"] or 0.0)) if equity is not None else None,
            low=median(lows) if lows else None,
            high=median(highs) if highs else None,
            currency=inputs.currency,
            components={"metrics": comps, "company": m, "current_multiples": cur},
            diagnostics={"n_metrics": len(bases), "price": price, "use": use},
            warnings=warnings,
        )


MODEL = MultiplesModel()
