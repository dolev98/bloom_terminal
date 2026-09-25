"""Assumptions (all optional, user-facing) -> ResolvedAssumptions (fully specified, with a derivation note per field).

Defaults follow Damodaran's FCFF "ginzu" logic: growth = consensus or historical CAGR fading linearly to the
terminal g (<= rf and <= policy cap), margin fading to the 5y average, tax fading effective -> marginal,
reinvestment via sales-to-capital or capex/D&A/NWC as % of revenue, WACC from components, terminal Gordon or
exit multiple, equity bridge = EV - debt - leases - minority + cash & STI + non-operating assets.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.valuation.inputs import InputsSnapshot
from app.valuation.policies import DEFAULT_POLICIES, Policies

Scenario = Literal["bear", "base", "bull", "custom"]


class WaccAssumptions(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    rf: float | None = Field(None, description="Risk-free rate (decimal)")
    beta: float | None = Field(None, description="Levered beta")
    erp: float | None = Field(None, description="Mature-market equity risk premium (decimal)")
    crp: float | None = Field(None, description="Country risk premium (decimal)")
    lambda_: float | None = Field(None, alias="lambda", description="Exposure to CRP (1 = full)")
    cost_of_debt: float | None = Field(None, description="Pre-tax cost of debt (decimal)")
    weight_debt: float | None = Field(None, description="D / (D + E), market weights")
    wacc: float | None = Field(None, description="Override: use this WACC directly")


class TerminalAssumptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["gordon", "exit_multiple"] | None = None
    g: float | None = Field(None, description="Terminal growth (decimal), capped at rf and the policy cap")
    multiple: float | None = Field(None, description="Exit multiple (EV/EBITDA by default)")
    multiple_metric: Literal["ebitda", "ebit", "revenue"] | None = None


class BridgeAssumptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    debt: float | None = None
    cash: float | None = Field(None, description="Cash + short-term investments")
    net_debt: float | None = Field(None, description="Override debt - cash in one number")
    leases: float | None = None
    minority: float | None = None
    non_operating_assets: float | None = None
    shares: float | None = Field(None, description="Diluted share count")
    dilution: float | None = Field(None, description="Extra shares from options/RSUs (added to shares)")


class Assumptions(BaseModel):
    """Every field optional: `resolve(inputs)` fills what is missing from the snapshot + policies."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    scenario: Scenario | None = None
    forecast_years: int | None = Field(None, ge=1, le=20)
    base_revenue: float | None = Field(None, description="Override starting revenue")
    base_ebit: float | None = Field(None, description="Override starting EBIT")
    revenue_growth: float | None = Field(None, description="Year-1 revenue growth (decimal)")
    revenue_growth_path: list[float] | None = Field(None, description="Explicit growth per forecast year")
    growth_fade: Literal["linear", "none"] | None = None
    target_ebit_margin: float | None = None
    margin_fade_years: int | None = Field(None, ge=1, le=20)
    tax_rate: float | None = Field(None, description="Effective tax rate year 1 (decimal)")
    tax_rate_marginal: float | None = None
    reinvestment_method: Literal["components", "sales_to_capital"] | None = None
    sales_to_capital: float | None = None
    capex_pct_revenue: float | None = None
    da_pct_revenue: float | None = None
    nwc_pct_revenue: float | None = Field(None, description="Change in NWC as % of change in revenue")
    sbc_pct_revenue: float | None = None
    sbc_as_cash_expense: bool | None = None
    mid_year: bool | None = None
    wacc: WaccAssumptions = Field(default_factory=WaccAssumptions)
    terminal: TerminalAssumptions = Field(default_factory=TerminalAssumptions)
    bridge: BridgeAssumptions = Field(default_factory=BridgeAssumptions)
    fair_value: float | None = Field(None, description="External model: a ready fair value per share")
    fair_value_low: float | None = None
    fair_value_high: float | None = None
    peer_stats: dict[str, dict[str, float]] | None = Field(
        None, description="{metric: {p25, median, p75}} for multiples"
    )
    history_stats: dict[str, dict[str, float]] | None = Field(
        None, description="Own-history multiples {metric: {p25, median, p75}}"
    )
    multiples_use: Literal["peers", "history", "both"] | None = None
    external_source: str | None = None
    custom: dict[str, Any] = Field(
        default_factory=dict, description="Model-specific assumptions for user models (custom.<key>)"
    )
    note: str | None = None

    # --- helpers ---------------------------------------------------------------
    def merged(self, other: Assumptions | dict | None) -> Assumptions:
        """Deep-merge `other` (non-None values win) over self, returning a new object."""
        if other is None:
            return self.model_copy(deep=True)
        o = other if isinstance(other, dict) else other.model_dump(exclude_none=True, by_alias=True)
        base = self.model_dump(exclude_none=True, by_alias=True)
        return Assumptions.model_validate(_deep_merge(base, o))

    def with_path(self, path: str, value: Any) -> Assumptions:
        """Return a copy with dotted `path` (e.g. 'wacc.beta', 'terminal.g') set to `value`."""
        d = self.model_dump(exclude_none=True, by_alias=True)
        cur = d
        parts = path.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value
        return Assumptions.model_validate(d)

    def get_path(self, path: str) -> Any:
        cur: Any = self.model_dump(by_alias=True)
        for p in path.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(p)
        return cur

    def checksum(self) -> str:
        payload = json.dumps(self.model_dump(exclude_none=True, by_alias=True), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def diff(self, other: Assumptions) -> dict[str, dict[str, Any]]:
        """{path: {"from": .., "to": ..}} for every leaf that differs between self and other."""
        a = _flatten(self.model_dump(exclude_none=True, by_alias=True))
        b = _flatten(other.model_dump(exclude_none=True, by_alias=True))
        out = {}
        for k in sorted(set(a) | set(b)):
            if a.get(k) != b.get(k):
                out[k] = {"from": a.get(k), "to": b.get(k)}
        return out

    def resolve(self, inputs: InputsSnapshot, policies: Policies | None = None) -> ResolvedAssumptions:
        return resolve(inputs, self, policies or DEFAULT_POLICIES)

    @classmethod
    def json_schema(cls) -> dict:
        return cls.model_json_schema(by_alias=True)


class ResolvedAssumptions(BaseModel):
    """Fully specified inputs for a DCF. `derivation` says where each default came from (provenance for numbers)."""

    scenario: str = "base"
    forecast_years: int
    base_revenue: float
    base_ebit: float
    base_margin: float
    growth_path: list[float]
    margin_path: list[float]
    tax_path: list[float]
    tax_marginal: float
    reinvestment_method: str
    sales_to_capital: float | None
    capex_pct: float
    da_pct: float
    nwc_pct: float
    sbc_pct: float
    sbc_as_cash_expense: bool
    mid_year: bool
    rf: float
    beta: float
    erp: float
    crp: float
    lambda_: float = 1.0
    cost_of_equity: float
    cost_of_debt: float
    weight_debt: float
    wacc: float
    terminal_method: str
    terminal_g: float
    terminal_multiple: float | None
    terminal_multiple_metric: str
    debt: float
    cash: float
    leases: float
    minority: float
    non_operating_assets: float
    shares: float
    dilution: float
    derivation: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @property
    def net_debt(self) -> float:
        return self.debt + self.leases - self.cash


# --- resolution -------------------------------------------------------------------


def _cagr(values: list[float | None], max_years: int = 5) -> float | None:
    vals = [v for v in values if v is not None and v > 0]
    if len(vals) < 2:
        return None
    vals = vals[-(max_years + 1) :]
    n = len(vals) - 1
    return (vals[-1] / vals[0]) ** (1.0 / n) - 1.0


def _avg_ratio(num: list[float | None], den: list[float | None], years: int = 5) -> float | None:
    pairs = [(a, b) for a, b in zip(num, den, strict=False) if a is not None and b]
    pairs = pairs[-years:]
    if not pairs:
        return None
    return sum(a / b for a, b in pairs) / len(pairs)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def resolve(inputs: InputsSnapshot, a: Assumptions, pol: Policies) -> ResolvedAssumptions:
    d: dict[str, str] = {}
    warn: list[str] = []
    n = a.forecast_years or pol.forecast_years
    d["forecast_years"] = "assumption" if a.forecast_years else "policy default"

    # --- starting point ---
    rev = a.base_revenue if a.base_revenue is not None else inputs.value("revenue")
    if rev is None or rev <= 0:
        warn.append("revenue missing: base_revenue=0")
        rev = 0.0
    d["base_revenue"] = "assumption" if a.base_revenue is not None else "statements (TTM/latest FY)"
    ebit = a.base_ebit if a.base_ebit is not None else inputs.value("operating_income")
    if ebit is None:
        ebit = 0.0
        warn.append("operating_income missing: base_ebit=0")
    if pol.rd_capitalize and inputs.value("research_development"):
        rd = inputs.series("research_development", 5)
        rd_vals = [x for x in rd if x is not None]
        if rd_vals:
            amort = sum(rd_vals) / len(rd_vals)
            ebit = ebit + rd_vals[-1] - amort
            d["rd_capitalization"] = (
                f"EBIT + R&D {rd_vals[-1]:.0f} - 5y straight-line amortization {amort:.0f}"
            )
    base_margin = ebit / rev if rev else 0.0

    # --- growth path ---
    g_hist = _cagr(inputs.series("revenue"))
    cons = inputs.consensus.revenue_growth_5y if inputs.consensus else None
    rf = a.wacc.rf if a.wacc.rf is not None else (inputs.macro.rf if inputs.macro.rf is not None else 0.04)
    d["rf"] = (
        "assumption"
        if a.wacc.rf is not None
        else ("macro (treasury/FRED)" if inputs.macro.rf is not None else "default 4%")
    )
    g_t = a.terminal.g if a.terminal.g is not None else min(rf, pol.g_cap)
    if g_t > rf:
        warn.append(f"terminal g {g_t:.3f} > rf {rf:.3f}: capped at rf")
        g_t = rf
    d["terminal_g"] = (
        "assumption (capped at rf)" if a.terminal.g is not None else f"min(rf, policy cap {pol.g_cap})"
    )
    if a.revenue_growth_path:
        path = list(a.revenue_growth_path)[:n]
        while len(path) < n:
            path.append(path[-1] if path else g_t)
        d["growth_path"] = "assumption (explicit path)"
    else:
        if a.revenue_growth is not None:
            g0, src = a.revenue_growth, "assumption"
        elif cons is not None:
            g0, src = cons, "consensus 5y"
        elif g_hist is not None:
            g0, src = _clamp(g_hist, -0.10, 0.50), "historical revenue CAGR (clamped -10%..50%)"
        else:
            g0, src = g_t, "terminal g (no history)"
        fade = a.growth_fade or "linear"
        if fade == "none":
            path = [g0] * n
        else:
            path = [g0 + (g_t - g0) * i / n for i in range(n)]
        d["growth_path"] = f"{src}, {fade} fade to terminal g"

    # --- margin path ---
    hist_margin = _avg_ratio(inputs.series("operating_income"), inputs.series("revenue"), 5)
    if a.target_ebit_margin is not None:
        tgt, src = a.target_ebit_margin, "assumption"
    elif hist_margin is not None:
        tgt, src = hist_margin, "5y average EBIT margin"
    else:
        tgt, src = base_margin, "current margin (no history)"
    fade_years = a.margin_fade_years or n
    margin_path = []
    for i in range(1, n + 1):
        if i >= fade_years:
            margin_path.append(tgt)
        else:
            margin_path.append(base_margin + (tgt - base_margin) * i / fade_years)
    d["margin_path"] = f"current {base_margin:.3f} -> target {tgt:.3f} ({src}) over {fade_years}y"

    # --- taxes ---
    t_marg = a.tax_rate_marginal if a.tax_rate_marginal is not None else (inputs.macro.tax_marginal or 0.21)
    eff = _avg_ratio(inputs.series("income_tax_expense"), inputs.series("pretax_income"), 3)
    if a.tax_rate is not None:
        t0, src = a.tax_rate, "assumption"
    elif eff is not None:
        t0, src = _clamp(eff, 0.0, 0.40), "3y effective rate (clamped 0..40%)"
    else:
        t0, src = t_marg, "marginal (no history)"
    tax_path = [t0 + (t_marg - t0) * i / max(n - 1, 1) for i in range(n)]
    d["tax_path"] = f"{src} {t0:.3f} -> marginal {t_marg:.3f}"

    # --- reinvestment ---
    capex_pct = a.capex_pct_revenue
    da_pct = a.da_pct_revenue
    nwc_pct = a.nwc_pct_revenue
    if capex_pct is None:
        capex_pct = _avg_ratio(inputs.series("capex"), inputs.series("revenue"), 3) or 0.0
        d["capex_pct"] = "3y avg capex/revenue"
    else:
        d["capex_pct"] = "assumption"
    if da_pct is None:
        da_pct = _avg_ratio(inputs.series("depreciation_amortization"), inputs.series("revenue"), 3) or 0.0
        d["da_pct"] = "3y avg D&A/revenue"
    else:
        d["da_pct"] = "assumption"
    if nwc_pct is None:
        ca, cl, rv = (
            inputs.series("total_current_assets"),
            inputs.series("total_current_liabilities"),
            inputs.series("revenue"),
        )
        nwc = [
            ((x or 0.0) - (y or 0.0)) if x is not None and y is not None else None
            for x, y in zip(ca, cl, strict=False)
        ]
        nwc_pct = _avg_ratio(nwc, rv, 3) or 0.0
        nwc_pct = _clamp(nwc_pct, -0.5, 0.5)
        d["nwc_pct"] = "3y avg NWC/revenue (applied to revenue change)"
    else:
        d["nwc_pct"] = "assumption"
    method = a.reinvestment_method or ("sales_to_capital" if a.sales_to_capital is not None else "components")
    stc = a.sales_to_capital
    if method == "sales_to_capital" and stc is None:
        ic = (inputs.value("total_equity") or 0.0) + inputs.total_debt() - inputs.cash_and_sti()
        stc = rev / ic if ic > 0 else 2.0
        d["sales_to_capital"] = "revenue / invested capital (equity + debt - cash)"
    sbc_cash = a.sbc_as_cash_expense if a.sbc_as_cash_expense is not None else pol.sbc_cash_expense
    sbc_pct = a.sbc_pct_revenue
    if sbc_pct is None:
        sbc_pct = _avg_ratio(inputs.series("stock_based_comp"), inputs.series("revenue"), 3) or 0.0
        d["sbc_pct"] = "3y avg SBC/revenue"

    # --- WACC ---
    erp = (
        a.wacc.erp
        if a.wacc.erp is not None
        else (inputs.macro.erp if inputs.macro.erp is not None else 0.045)
    )
    d["erp"] = (
        "assumption"
        if a.wacc.erp is not None
        else ("macro (Damodaran)" if inputs.macro.erp is not None else "default 4.5%")
    )
    crp = a.wacc.crp if a.wacc.crp is not None else (inputs.macro.crp or 0.0)
    lam = a.wacc.lambda_ if a.wacc.lambda_ is not None else 1.0
    if a.wacc.beta is not None:
        beta, src = a.wacc.beta, "assumption"
    elif inputs.market.beta_candidates:
        cands = sorted(inputs.market.beta_candidates.values())
        beta = cands[len(cands) // 2]
        src = f"median of candidates {inputs.market.beta_candidates}"
    else:
        beta, src = 1.0, "default 1.0 (no candidates)"
    beta = _clamp(beta, 0.3, 3.0)
    d["beta"] = src
    ke = rf + beta * erp + lam * crp
    debt = a.bridge.debt if a.bridge.debt is not None else inputs.total_debt()
    leases = (
        a.bridge.leases
        if a.bridge.leases is not None
        else ((inputs.value("long_term_lease_liabilities") or 0.0) if pol.leases_as_debt else 0.0)
    )
    cash = a.bridge.cash if a.bridge.cash is not None else inputs.cash_and_sti()
    if a.bridge.net_debt is not None:
        debt, cash, leases = max(a.bridge.net_debt, 0.0), max(-a.bridge.net_debt, 0.0), 0.0
        d["bridge"] = "net_debt override"
    else:
        d["bridge"] = "statements: ST+LT debt, leases (policy), cash + STI"
    if a.wacc.cost_of_debt is not None:
        kd, src = a.wacc.cost_of_debt, "assumption"
    else:
        ie = inputs.value("interest_expense")
        kd_hist = ie / (debt + leases) if ie and (debt + leases) > 0 else None
        if kd_hist is not None and rf <= kd_hist <= rf + 0.12:
            kd, src = kd_hist, "interest expense / total debt"
        else:
            kd, src = rf + 0.02, "rf + 2% synthetic spread"
    d["cost_of_debt"] = src
    if a.wacc.weight_debt is not None:
        wd = _clamp(a.wacc.weight_debt, 0.0, 0.95)
    else:
        mcap = inputs.market.market_cap or ((inputs.market.price or 0.0) * (inputs.shares() or 0.0))
        total = mcap + debt + leases
        wd = (debt + leases) / total if total > 0 else 0.0
    wacc = a.wacc.wacc if a.wacc.wacc is not None else (1 - wd) * ke + wd * kd * (1 - t_marg)
    d["wacc"] = (
        "assumption override"
        if a.wacc.wacc is not None
        else f"(1-wd)*ke + wd*kd*(1-t): ke={ke:.4f}, kd={kd:.4f}, wd={wd:.3f}"
    )
    if wacc <= g_t:
        warn.append(f"wacc {wacc:.4f} <= terminal g {g_t:.4f}; g reduced to wacc - 1%")
        g_t = wacc - 0.01

    # --- terminal ---
    method_t = a.terminal.method or "gordon"
    mult = a.terminal.multiple
    if method_t == "exit_multiple" and mult is None:
        mult = 10.0
        warn.append("exit multiple missing: default 10x")
    shares = a.bridge.shares if a.bridge.shares is not None else (inputs.shares() or 0.0)
    if shares <= 0:
        warn.append("share count missing: value per share undefined")
    d["shares"] = (
        "assumption"
        if a.bridge.shares is not None
        else "diluted weighted shares (statements) / shares outstanding"
    )

    return ResolvedAssumptions(
        scenario=a.scenario or "base",
        forecast_years=n,
        base_revenue=rev,
        base_ebit=ebit,
        base_margin=base_margin,
        growth_path=path,
        margin_path=margin_path,
        tax_path=tax_path,
        tax_marginal=t_marg,
        reinvestment_method=method,
        sales_to_capital=stc,
        capex_pct=capex_pct,
        da_pct=da_pct,
        nwc_pct=nwc_pct,
        sbc_pct=sbc_pct,
        sbc_as_cash_expense=sbc_cash,
        mid_year=a.mid_year if a.mid_year is not None else pol.mid_year,
        rf=rf,
        beta=beta,
        erp=erp,
        crp=crp,
        lambda_=lam,
        cost_of_equity=ke,
        cost_of_debt=kd,
        weight_debt=wd,
        wacc=wacc,
        terminal_method=method_t,
        terminal_g=g_t,
        terminal_multiple=mult,
        terminal_multiple_metric=a.terminal.multiple_metric or "ebitda",
        debt=debt,
        cash=cash,
        leases=leases,
        minority=a.bridge.minority
        if a.bridge.minority is not None
        else (inputs.value("minority_interest") or 0.0),
        non_operating_assets=a.bridge.non_operating_assets or 0.0,
        shares=shares,
        dilution=a.bridge.dilution or 0.0,
        derivation=d,
        warnings=warn,
    )


def _deep_merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def _flatten(d: dict, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def is_finite(x: float | None) -> bool:
    return x is not None and math.isfinite(x)
