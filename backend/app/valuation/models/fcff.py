"""Multi-stage FCFF DCF with fades.

FCFF_t = EBIT_t (1 - tax_t) + D&A_t - capex_t - dNWC_t [- SBC_t when policy treats SBC as cash]
       (or, with `sales_to_capital`: NOPAT_t - dRevenue_t / sales_to_capital)
Terminal: Gordon FCFF_{N+1} / (WACC - g) with g <= rf, or exit multiple x metric_N.
Equity = EV - debt - leases - minority + cash & STI + non-operating assets; per share on diluted shares (+ dilution).
"""

from __future__ import annotations

from typing import ClassVar

from app.valuation.assumptions import Assumptions, ResolvedAssumptions
from app.valuation.inputs import InputsSnapshot
from app.valuation.models.base import BaseValuationModel, ValuationResult


def project(r: ResolvedAssumptions) -> dict:
    """Year-by-year projection + discounting. Pure function of a ResolvedAssumptions (used by reverse DCF and grids)."""
    rows = []
    rev_prev = r.base_revenue
    pv_sum = 0.0
    for i in range(1, r.forecast_years + 1):
        g = r.growth_path[i - 1]
        rev = rev_prev * (1 + g)
        margin = r.margin_path[i - 1]
        ebit = rev * margin
        t = r.tax_path[i - 1]
        tax = ebit * t if ebit > 0 else 0.0
        nopat = ebit - tax
        da = rev * r.da_pct
        sbc = rev * r.sbc_pct if r.sbc_as_cash_expense else 0.0
        if r.reinvestment_method == "sales_to_capital" and r.sales_to_capital:
            reinvest = (rev - rev_prev) / r.sales_to_capital
            capex, dnwc = reinvest + da, 0.0  # decomposition for display only
            fcff = nopat - reinvest - sbc
        else:
            capex = rev * r.capex_pct
            dnwc = (rev - rev_prev) * r.nwc_pct
            reinvest = capex - da + dnwc
            fcff = nopat + da - capex - dnwc - sbc
        exp = i - 0.5 if r.mid_year else i
        df = 1.0 / (1.0 + r.wacc) ** exp
        pv = fcff * df
        pv_sum += pv
        rows.append(
            {
                "year": i,
                "revenue": rev,
                "growth": g,
                "ebit": ebit,
                "margin": margin,
                "tax_rate": t,
                "nopat": nopat,
                "da": da,
                "capex": capex,
                "dnwc": dnwc,
                "sbc": sbc,
                "reinvestment": reinvest,
                "fcff": fcff,
                "discount_factor": df,
                "pv": pv,
            }
        )
        rev_prev = rev
    last = rows[-1]
    warnings: list[str] = []
    if r.terminal_method == "exit_multiple":
        metric = {"ebitda": last["ebit"] + last["da"], "ebit": last["ebit"], "revenue": last["revenue"]}[
            r.terminal_multiple_metric
        ]
        tv = (r.terminal_multiple or 0.0) * metric
        tv_note = f"{r.terminal_multiple}x {r.terminal_multiple_metric} {metric:,.0f}"
        fcff_terminal = None
    else:
        spread = r.wacc - r.terminal_g
        if spread <= 0.005:
            warnings.append("WACC - g below 0.5%: terminal value unstable")
            spread = max(spread, 0.005)
        fcff_terminal = last["fcff"] * (1 + r.terminal_g)
        tv = fcff_terminal / spread
        tv_note = f"FCFF_N+1 {fcff_terminal:,.0f} / (wacc {r.wacc:.4f} - g {r.terminal_g:.4f})"
    df_tv = 1.0 / (1.0 + r.wacc) ** r.forecast_years
    pv_tv = tv * df_tv
    ev = pv_sum + pv_tv
    # implied terminal ROIC: g = reinvestment rate x ROIC -> ROIC = g / (reinvestment / NOPAT)
    rr = last["reinvestment"] / last["nopat"] if last["nopat"] else None
    implied_roic = (r.terminal_g / rr) if rr and rr > 0 else None
    return {
        "years": rows,
        "pv_fcff": pv_sum,
        "terminal_value": tv,
        "pv_terminal": pv_tv,
        "terminal_note": tv_note,
        "fcff_terminal": fcff_terminal,
        "ev": ev,
        "pv_terminal_share": (pv_tv / ev) if ev else None,
        "terminal_reinvestment_rate": rr,
        "implied_terminal_roic": implied_roic,
        "warnings": warnings,
    }


def bridge(ev: float, r: ResolvedAssumptions) -> dict:
    equity = ev - r.debt - r.leases - r.minority + r.cash + r.non_operating_assets
    shares = r.shares + r.dilution
    vps = equity / shares if shares > 0 else None
    return {
        "ev": ev,
        "debt": r.debt,
        "leases": r.leases,
        "minority": r.minority,
        "cash": r.cash,
        "non_operating_assets": r.non_operating_assets,
        "equity_value": equity,
        "shares": shares,
        "value_per_share": vps,
    }


class FCFFModel(BaseValuationModel):
    id: ClassVar[str] = "fcff"
    name: ClassVar[str] = "FCFF multi-stage DCF"
    version: ClassVar[str] = "1.0"
    inputs_required: ClassVar[set[str]] = {"revenue", "operating_income"}
    reference: ClassVar[str | None] = "base_dcf"

    def run(self, inputs: InputsSnapshot, a: Assumptions, policies=None) -> ValuationResult:
        r = a.resolve(inputs, policies)
        return self.run_resolved(inputs, r)

    def run_resolved(self, inputs: InputsSnapshot, r: ResolvedAssumptions) -> ValuationResult:
        warnings = list(r.warnings)
        if r.base_revenue <= 0:
            return ValuationResult(
                model_id=self.id,
                model_version=self.version,
                scenario=r.scenario,
                currency=inputs.currency,
                warnings=warnings + ["cannot value: no revenue"],
                diagnostics={"resolved": r.model_dump()},
            )
        proj = project(r)
        warnings += proj.pop("warnings")
        br = bridge(proj["ev"], r)
        if br["value_per_share"] is None:
            warnings.append("share count missing")
        return ValuationResult(
            model_id=self.id,
            model_version=self.version,
            scenario=r.scenario,
            value_per_share=br["value_per_share"],
            ev=proj["ev"],
            equity_value=br["equity_value"],
            currency=inputs.currency,
            components={"bridge": br, "projection": proj},
            diagnostics={
                "wacc": r.wacc,
                "cost_of_equity": r.cost_of_equity,
                "terminal_g": r.terminal_g,
                "pv_terminal_share": proj["pv_terminal_share"],
                "implied_terminal_roic": proj["implied_terminal_roic"],
                "resolved": r.model_dump(),
                "derivation": r.derivation,
            },
            warnings=warnings,
        )


MODEL = FCFFModel()
