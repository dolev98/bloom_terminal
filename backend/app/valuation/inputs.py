"""InputsSnapshot: a frozen, content-hashed picture of everything a valuation model may consume.

`build_inputs(ticker)` assembles it from the statements service (approved facts only), the quote cache
(licensed price with source/ts; OHLCV last close as fallback), the macro providers (Treasury rf, Damodaran
ERP/CRP with a `fred:DGS10` fallback) and optional consensus. Every number carries provenance.

Tests build snapshots directly (see `synthetic_inputs` in tests/valuation/conftest.py).
"""

from __future__ import annotations

import hashlib
import logging
import math
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)

CANONICAL_FIELDS = (
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_income",
    "depreciation_amortization",
    "stock_based_comp",
    "interest_expense",
    "pretax_income",
    "income_tax_expense",
    "net_income",
    "eps_diluted",
    "shares_diluted_weighted",
    "shares_outstanding",
    "cash_and_equivalents",
    "short_term_investments",
    "total_current_assets",
    "total_current_liabilities",
    "short_term_debt",
    "long_term_debt",
    "long_term_lease_liabilities",
    "minority_interest",
    "total_equity",
    "total_assets",
    "cfo",
    "capex",
    "fcf",
    "dividends_paid",
    "share_repurchases",
    "research_development",
)


class StatementPeriod(BaseModel):
    model_config = ConfigDict(frozen=True)

    period_end: date
    fiscal_year: int | None = None
    fiscal_period: str | None = None  # FY | Q1..Q4 | TTM
    fields: dict[str, float | None] = Field(default_factory=dict)

    def get(self, key: str) -> float | None:
        v = self.fields.get(key)
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


class MarketData(BaseModel):
    model_config = ConfigDict(frozen=True)

    price: float | None = None
    price_source: str | None = None
    price_ts: datetime | None = None
    currency: str = "USD"
    shares_outstanding: float | None = None
    market_cap: float | None = None
    beta_candidates: dict[str, float] = Field(
        default_factory=dict
    )  # {"own_2y_weekly": 1.1, "industry": 1.05}
    high_52w: float | None = None
    low_52w: float | None = None


class MacroData(BaseModel):
    model_config = ConfigDict(frozen=True)

    rf: float | None = None  # decimal, e.g. 0.042
    erp: float | None = None
    crp: float | None = None
    tax_marginal: float | None = None
    aaa_yield: float | None = None
    as_of: dict[str, str] = Field(default_factory=dict)


class Consensus(BaseModel):
    model_config = ConfigDict(frozen=True)

    eps_next_year: float | None = None
    revenue_growth_5y: float | None = None  # decimal
    target_mean: float | None = None
    target_low: float | None = None
    target_high: float | None = None
    n_analysts: int | None = None
    source: str | None = None


class InputsSnapshot(BaseModel):
    """Frozen inputs. `content_hash` identifies the snapshot in valuation_runs / fair_value_daily."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    as_of: date
    currency: str = "USD"
    annual: list[StatementPeriod] = Field(default_factory=list)  # oldest -> newest
    quarterly: list[StatementPeriod] = Field(default_factory=list)
    ttm: StatementPeriod | None = None
    market: MarketData = Field(default_factory=MarketData)
    macro: MacroData = Field(default_factory=MacroData)
    consensus: Consensus | None = None
    provenance: dict[str, dict[str, Any]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @property
    def content_hash(self) -> str:
        payload = self.model_dump_json(exclude={"provenance", "warnings"})
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    # --- convenience accessors -------------------------------------------------
    @property
    def latest(self) -> StatementPeriod | None:
        """TTM if present, else the newest annual period."""
        if self.ttm is not None:
            return self.ttm
        return self.annual[-1] if self.annual else None

    @property
    def latest_annual(self) -> StatementPeriod | None:
        return self.annual[-1] if self.annual else None

    def value(self, key: str, default: float | None = None) -> float | None:
        """Latest value of a canonical field: TTM first, then newest annual (balance-sheet items fall back the same way)."""
        for p in (self.ttm, self.latest_annual):
            if p is not None:
                v = p.get(key)
                if v is not None:
                    return v
        return default

    def series(self, key: str, years: int | None = None) -> list[float | None]:
        vals = [p.get(key) for p in self.annual]
        return vals[-years:] if years else vals

    def missing(self, keys: set[str] | list[str]) -> list[str]:
        return [k for k in keys if self.value(k) is None]

    def total_debt(self) -> float:
        return (self.value("short_term_debt") or 0.0) + (self.value("long_term_debt") or 0.0)

    def cash_and_sti(self) -> float:
        return (self.value("cash_and_equivalents") or 0.0) + (self.value("short_term_investments") or 0.0)

    def shares(self) -> float | None:
        return (
            self.value("shares_diluted_weighted")
            or self.market.shares_outstanding
            or self.value("shares_outstanding")
        )

    def summary(self) -> dict:
        return {
            "ticker": self.ticker,
            "as_of": self.as_of.isoformat(),
            "hash": self.content_hash,
            "annual_periods": len(self.annual),
            "ttm": self.ttm.period_end.isoformat() if self.ttm else None,
            "price": self.market.price,
            "price_source": self.market.price_source,
            "price_ts": self.market.price_ts.isoformat() if self.market.price_ts else None,
            "macro": self.macro.model_dump(),
            "warnings": list(self.warnings),
        }


# --- builders ------------------------------------------------------------------


def _periods_from_contract(data: dict) -> list[StatementPeriod]:
    """Convert the statements-service contract ({"periods": [...], "fields": {f: [..]}}) into StatementPeriod rows."""
    periods = data.get("periods") or []
    fields = data.get("fields") or {}
    out: list[StatementPeriod] = []
    for i, p in enumerate(periods):
        vals = {}
        for f, arr in fields.items():
            v = arr[i] if i < len(arr) else None
            vals[f] = None if v is None else float(v)
        pe = p.get("period_end")
        pe_d = date.fromisoformat(pe) if isinstance(pe, str) else pe
        out.append(
            StatementPeriod(
                period_end=pe_d,
                fiscal_year=p.get("fiscal_year"),
                fiscal_period=p.get("fiscal_period"),
                fields=vals,
            )
        )
    out.sort(key=lambda x: x.period_end)
    return out


async def _statements(ticker: str, period_type: str) -> tuple[dict | None, str | None]:
    """Lazy import of the statements service (built by another module); returns (data, warning)."""
    try:
        from app.statements.service import get_statements  # type: ignore[import-not-found]
    except ImportError:
        return None, "statements service not available"
    try:
        data = await get_statements(ticker, period_type=period_type, restated=True, approved_only=True)
    except Exception as e:  # provider/data errors must not kill the snapshot
        return None, f"statements {period_type}: {type(e).__name__}: {e}"[:200]
    return data, None


def _price_from_quotes(ticker: str) -> tuple[float | None, str | None, datetime | None]:
    from app.market.service import cached_quotes, read_ohlcv

    qs = cached_quotes([ticker])
    if qs:
        q = qs[0]
        ts = q.get("ts")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return float(q["last"]), str(q.get("source") or ""), ts
    df, _ = read_ohlcv(ticker, "1d")
    if df.is_empty():
        return None, None, None
    last = df.tail(1)
    return float(last["close"][0]), "ohlcv_close", last["ts"][0]


def _range_52w(ticker: str) -> tuple[float | None, float | None]:
    from app.market.service import read_ohlcv

    df, _ = read_ohlcv(ticker, "1d")
    if df.is_empty():
        return None, None
    tail = df.tail(252)
    return float(tail["high"].max()), float(tail["low"].min())


def own_beta(ticker: str, benchmark: str = "^GSPC", years: int = 2, blume: bool = True) -> float | None:
    """Regression beta from stored daily OHLCV resampled to weekly closes (Blume-adjusted by default)."""
    import numpy as np
    import polars as pl

    from app.market.service import read_ohlcv

    a, _ = read_ohlcv(ticker, "1d")
    b, _ = read_ohlcv(benchmark, "1d")
    if a.is_empty() or b.is_empty():
        return None
    start = datetime.now(tz=UTC).replace(tzinfo=None).date().replace(year=datetime.now(tz=UTC).year - years)
    a = a.filter(pl.col("ts") >= datetime(start.year, start.month, start.day))
    b = b.filter(pl.col("ts") >= datetime(start.year, start.month, start.day))
    wa = a.group_by_dynamic("ts", every="1w").agg(pl.col("close").last().alias("a"))
    wb = b.group_by_dynamic("ts", every="1w").agg(pl.col("close").last().alias("b"))
    j = wa.join(wb, on="ts", how="inner").sort("ts")
    if j.height < 30:
        return None
    ra = np.diff(np.log(j["a"].to_numpy()))
    rb = np.diff(np.log(j["b"].to_numpy()))
    var = float(np.var(rb, ddof=1))
    if var <= 0:
        return None
    beta = float(np.cov(ra, rb, ddof=1)[0, 1] / var)
    return 0.67 * beta + 0.33 if blume else beta


async def build_inputs(ticker: str, benchmark: str = "^GSPC") -> InputsSnapshot:
    """Assemble the snapshot from statements, quotes, macro providers and (optional) consensus. Never raises on missing data."""
    from app.valuation.providers import damodaran, treasury

    ticker = ticker.upper()
    warnings: list[str] = []
    prov: dict[str, dict[str, Any]] = {}

    annual: list[StatementPeriod] = []
    quarterly: list[StatementPeriod] = []
    ttm: StatementPeriod | None = None
    currency = "USD"
    fy, w = await _statements(ticker, "FY")
    if w:
        warnings.append(w)
    if fy:
        annual = _periods_from_contract(fy)[-10:]
        currency = fy.get("currency") or currency
        prov["statements"] = {
            "source": "statements.service",
            "period_type": "FY",
            "n": len(annual),
            "approved_only": True,
        }
    q, w = await _statements(ticker, "Q")
    if q:
        quarterly = _periods_from_contract(q)[-8:]
    t, w = await _statements(ticker, "TTM")
    if t:
        rows = _periods_from_contract(t)
        if rows:
            ttm = StatementPeriod(period_end=rows[-1].period_end, fiscal_period="TTM", fields=rows[-1].fields)
    if not annual and not ttm:
        warnings.append("no approved statements for ticker; fundamental models will not run")

    price, src, ts = _price_from_quotes(ticker)
    if price is None:
        warnings.append("no price available (quote cache empty and no OHLCV)")
    else:
        prov["price"] = {"source": src, "ts": ts.isoformat() if ts else None}
    hi, lo = _range_52w(ticker)
    betas: dict[str, float] = {}
    try:
        ob = own_beta(ticker, benchmark)
        if ob is not None:
            betas["own_2y_weekly_blume"] = round(ob, 4)
    except Exception as e:  # pragma: no cover - defensive
        log.debug("own beta failed: %s", e)
    shares = None
    for p in (ttm, annual[-1] if annual else None):
        if p is not None and p.get("shares_outstanding"):
            shares = p.get("shares_outstanding")
            break
    market = MarketData(
        price=price,
        price_source=src,
        price_ts=ts.replace(tzinfo=None) if ts and ts.tzinfo else ts,
        currency=currency,
        shares_outstanding=shares,
        market_cap=(price * shares) if price and shares else None,
        beta_candidates=betas,
        high_52w=hi,
        low_52w=lo,
    )

    macro_kw: dict[str, Any] = {}
    as_of: dict[str, str] = {}
    try:
        rf = await treasury.get_rf()
        if rf:
            macro_kw["rf"] = rf["value"]
            as_of["rf"] = str(rf.get("as_of"))
            prov["rf"] = rf
    except Exception as e:
        warnings.append(f"rf: {e}"[:120])
    try:
        erp = await damodaran.get_erp()
        if erp:
            macro_kw["erp"] = erp["value"]
            as_of["erp"] = str(erp.get("as_of"))
            prov["erp"] = erp
    except Exception as e:
        warnings.append(f"erp: {e}"[:120])
    try:
        country = "Israel" if ticker.endswith(".TA") else "United States"
        crp = await damodaran.get_crp(country)
        if crp is not None:
            macro_kw["crp"] = crp["value"]
            as_of["crp"] = str(crp.get("as_of"))
            prov["crp"] = crp
    except Exception as e:
        warnings.append(f"crp: {e}"[:120])
    macro_kw["tax_marginal"] = 0.23 if ticker.endswith(".TA") else 0.21
    prov["tax_marginal"] = {"source": "statutory", "note": "IL 23% / US federal 21%"}
    try:
        ind_beta = await damodaran.industry_beta_for(ticker)
        if ind_beta is not None:
            betas["industry_unlevered"] = round(ind_beta, 4)
            market = market.model_copy(update={"beta_candidates": betas})
    except Exception as e:  # pragma: no cover - defensive
        log.debug("industry beta failed: %s", e)
    macro = MacroData(as_of=as_of, **macro_kw)

    consensus = None
    hook = CONSENSUS_HOOK
    if hook is not None:
        try:
            c = await hook(ticker)
            if c:
                consensus = Consensus(**c)
                prov["consensus"] = {"source": consensus.source}
        except Exception as e:
            warnings.append(f"consensus: {e}"[:120])

    return InputsSnapshot(
        ticker=ticker,
        as_of=datetime.now(tz=UTC).date(),
        currency=currency,
        annual=annual,
        quarterly=quarterly,
        ttm=ttm,
        market=market,
        macro=macro,
        consensus=consensus,
        provenance=prov,
        warnings=warnings,
    )


# Optional async hook `(ticker) -> dict | None` returning Consensus fields; set by an integrator (e.g. FMP estimates).
CONSENSUS_HOOK: Any = None
