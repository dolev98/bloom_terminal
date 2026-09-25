"""Ratios over the canonical frame (pure functions; one dict per period).

Flows are annualised for Q (x4) and H (x2) in return/coverage ratios. Balance-sheet averages and YoY / Piotroski
comparisons use the year-ago period (FY: previous row, Q/TTM: 4 rows back, H: 2 rows back).
"""

from __future__ import annotations

import polars as pl

from app.statements.periods import period_label

ANNUALIZE = {"FY": 1.0, "TTM": 1.0, "Q": 4.0, "H": 2.0}
DAYS = {"FY": 365, "TTM": 365, "Q": 91, "H": 182}
PRIOR_LAG = {"FY": 1, "TTM": 4, "Q": 4, "H": 2}
DEFAULT_TAX_RATE = 0.21

RATIO_KEYS = [
    "gross_margin",
    "operating_margin",
    "ebitda_margin",
    "net_margin",
    "fcf_margin",
    "roe",
    "roa",
    "roic",
    "fcf",
    "fcf_conversion",
    "capex_intensity",
    "sbc_pct_revenue",
    "net_debt",
    "net_debt_to_ebitda",
    "debt_to_equity",
    "interest_coverage",
    "working_capital",
    "current_ratio",
    "quick_ratio",
    "dso",
    "dio",
    "dpo",
    "asset_turnover",
    "altman_z",
    "altman_z2",
    "piotroski_f",
    "revenue_yoy",
    "net_income_yoy",
    "eps_yoy",
    "effective_tax_rate",
]


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _avg(cur: float | None, prev: float | None) -> float | None:
    if cur is None:
        return None
    return cur if prev is None else (cur + prev) / 2


def _sum(*vals: float | None) -> float | None:
    present = [v for v in vals if v is not None]
    return sum(present) if present else None


def _mul(a: float | None, k: float) -> float | None:
    return None if a is None else a * k


def _growth(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


def effective_tax_rate(tax: float | None, pretax: float | None) -> float:
    """Effective rate clamped to [0, 0.35]; statutory fallback when pre-tax income is missing or <= 0."""
    if tax is None or pretax is None or pretax <= 0:
        return DEFAULT_TAX_RATE
    return min(max(tax / pretax, 0.0), 0.35)


def compute_ratios(frame: pl.DataFrame, period_type: str, market_cap: float | None = None) -> list[dict]:
    if frame.is_empty():
        return []
    rows = frame.sort("period_end").to_dicts()
    ann = ANNUALIZE.get(period_type, 1.0)
    days = DAYS.get(period_type, 365)
    lag = PRIOR_LAG.get(period_type, 1)
    out = []
    for i, r in enumerate(rows):
        p = rows[i - lag] if i - lag >= 0 else None
        out.append(ratios_for(r, p, ann=ann, days=days, market_cap=market_cap))
    return out


def ratios_for(
    r: dict, p: dict | None, *, ann: float = 1.0, days: int = 365, market_cap: float | None = None
) -> dict:
    g = r.get
    pg = p.get if p else (lambda _k: None)
    rev, cogs, gp, oi = g("revenue"), g("cost_of_revenue"), g("gross_profit"), g("operating_income")
    ebitda, ni, cfo, capex, fcf = g("ebitda"), g("net_income"), g("cfo"), g("capex"), g("fcf")
    ta, tl, te, mi = g("total_assets"), g("total_liabilities"), g("total_equity"), g("minority_interest")
    cash, sti, ar, inv, ap = (
        g("cash_and_equivalents"),
        g("short_term_investments"),
        g("accounts_receivable"),
        g("inventory"),
        g("accounts_payable"),
    )
    std, ltd, lease = g("short_term_debt"), g("long_term_debt"), g("long_term_lease_liabilities")
    tca, tcl, re_ = g("total_current_assets"), g("total_current_liabilities"), g("retained_earnings")
    if fcf is None and cfo is not None and capex is not None:
        fcf = cfo - capex
    if ebitda is None and oi is not None and g("depreciation_amortization") is not None:
        ebitda = oi + g("depreciation_amortization")
    if gp is None and rev is not None and cogs is not None:
        gp = rev - cogs
    if tl is None and ta is not None and te is not None:
        tl = ta - te

    tax_rate = effective_tax_rate(g("income_tax_expense"), g("pretax_income"))
    debt = _sum(std, ltd, lease)
    net_debt = None if cash is None and debt is None else (debt or 0.0) - (cash or 0.0) - (sti or 0.0)
    equity_total = _sum(te, mi)
    invested = None if equity_total is None else equity_total + (debt or 0.0) - (cash or 0.0) - (sti or 0.0)
    invested_p = None
    if p and _sum(pg("total_equity"), pg("minority_interest")) is not None:
        invested_p = (
            _sum(pg("total_equity"), pg("minority_interest"))
            + (_sum(pg("short_term_debt"), pg("long_term_debt"), pg("long_term_lease_liabilities")) or 0.0)
            - (pg("cash_and_equivalents") or 0.0)
            - (pg("short_term_investments") or 0.0)
        )
    nopat = None if oi is None else oi * ann * (1 - tax_rate)
    wc = None if tca is None or tcl is None else tca - tcl

    res: dict = {
        "period_end": r["period_end"].isoformat()
        if hasattr(r["period_end"], "isoformat")
        else r["period_end"],
        "fiscal_year": r.get("fiscal_year"),
        "fiscal_period": r.get("fiscal_period"),
        "label": period_label(
            r.get("period_type", "FY"), r.get("fiscal_year"), r.get("fiscal_period"), r["period_end"]
        ),
        "gross_margin": _div(gp, rev),
        "operating_margin": _div(oi, rev),
        "ebitda_margin": _div(ebitda, rev),
        "net_margin": _div(ni, rev),
        "fcf_margin": _div(fcf, rev),
        "roe": _div(_mul(ni, ann), _avg(te, pg("total_equity"))),
        "roa": _div(_mul(ni, ann), _avg(ta, pg("total_assets"))),
        "roic": _div(nopat, _avg(invested, invested_p)),
        "fcf": fcf,
        "fcf_conversion": _div(fcf, ni) if ni is not None and ni > 0 else None,
        "capex_intensity": _div(capex, rev),
        "sbc_pct_revenue": _div(g("stock_based_comp"), rev),
        "net_debt": net_debt,
        "net_debt_to_ebitda": _div(net_debt, _mul(ebitda, ann)),
        "debt_to_equity": _div(debt, te),
        "interest_coverage": _div(oi, g("interest_expense")),
        "working_capital": wc,
        "current_ratio": _div(tca, tcl),
        "quick_ratio": _div(_sum(cash, sti, ar), tcl),
        "dso": _mul(_div(ar, rev), days),
        "dio": _mul(_div(inv, cogs), days),
        "dpo": _mul(_div(ap, cogs), days),
        "asset_turnover": _div(_mul(rev, ann), _avg(ta, pg("total_assets"))),
        "altman_z": altman_z(wc, re_, _mul(oi, ann), _mul(rev, ann), ta, tl, market_cap),
        "altman_z2": altman_z2(wc, re_, _mul(oi, ann), ta, tl, te),
        "piotroski_f": piotroski(r, p) if p else None,
        "revenue_yoy": _growth(rev, pg("revenue")),
        "net_income_yoy": _growth(ni, pg("net_income")),
        "eps_yoy": _growth(g("eps_diluted"), pg("eps_diluted")),
        "effective_tax_rate": tax_rate,
    }
    return res


def altman_z(wc, re_, ebit, rev, ta, tl, market_cap) -> float | None:
    """Original Altman Z (public manufacturers): needs market value of equity."""
    if None in (wc, re_, ebit, rev, ta, tl) or ta == 0 or tl == 0 or market_cap is None:
        return None
    return 1.2 * wc / ta + 1.4 * re_ / ta + 3.3 * ebit / ta + 0.6 * market_cap / tl + 1.0 * rev / ta


def altman_z2(wc, re_, ebit, ta, tl, book_equity) -> float | None:
    """Altman Z'' (non-manufacturers / emerging markets, book equity; no +3.25 constant)."""
    if None in (wc, re_, ebit, ta, tl, book_equity) or ta == 0 or tl == 0:
        return None
    return 6.56 * wc / ta + 3.26 * re_ / ta + 6.72 * ebit / ta + 1.05 * book_equity / tl


def piotroski(r: dict, p: dict) -> int | None:
    """Piotroski F-score 0..9 vs the prior comparable period. None when fewer than 5 signals are computable."""
    g, pg = r.get, p.get
    signals: list[bool | None] = []
    roa, roa_p = _div(g("net_income"), g("total_assets")), _div(pg("net_income"), pg("total_assets"))
    signals.append(None if roa is None else roa > 0)
    cfo = g("cfo")
    signals.append(None if cfo is None else cfo > 0)
    signals.append(None if roa is None or roa_p is None else roa > roa_p)
    signals.append(None if cfo is None or g("net_income") is None else cfo > g("net_income"))
    lev = _div(g("long_term_debt") or 0.0, g("total_assets"))
    lev_p = _div(pg("long_term_debt") or 0.0, pg("total_assets"))
    signals.append(None if lev is None or lev_p is None else lev <= lev_p)
    cr, cr_p = (
        _div(g("total_current_assets"), g("total_current_liabilities")),
        _div(pg("total_current_assets"), pg("total_current_liabilities")),
    )
    signals.append(None if cr is None or cr_p is None else cr > cr_p)
    sh = g("shares_outstanding") if g("shares_outstanding") is not None else g("shares_basic_weighted")
    sh_p = pg("shares_outstanding") if pg("shares_outstanding") is not None else pg("shares_basic_weighted")
    signals.append(None if sh is None or sh_p is None else sh <= sh_p)
    gp = (
        g("gross_profit")
        if g("gross_profit") is not None
        else (
            None
            if g("revenue") is None or g("cost_of_revenue") is None
            else g("revenue") - g("cost_of_revenue")
        )
    )
    gp_p = (
        pg("gross_profit")
        if pg("gross_profit") is not None
        else (
            None
            if pg("revenue") is None or pg("cost_of_revenue") is None
            else pg("revenue") - pg("cost_of_revenue")
        )
    )
    gm, gm_p = _div(gp, g("revenue")), _div(gp_p, pg("revenue"))
    signals.append(None if gm is None or gm_p is None else gm > gm_p)
    at, at_p = _div(g("revenue"), g("total_assets")), _div(pg("revenue"), pg("total_assets"))
    signals.append(None if at is None or at_p is None else at > at_p)
    known = [s for s in signals if s is not None]
    if len(known) < 5:
        return None
    return sum(1 for s in known if s)
