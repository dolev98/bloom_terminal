"""Canonical statement fields, their kinds (flow / stock / per-share / average) and the FactRow record every
ingestion path produces. Values are stored in base units (not thousands); `unit_scale` only records how the
source expressed them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

SCHEMA_VERSION = 1

IS_FIELDS: list[str] = [
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "rnd_expense",
    "sga_expense",
    "other_operating_expense",
    "operating_income",
    "depreciation_amortization",
    "stock_based_comp",
    "interest_expense",
    "interest_income",
    "other_nonoperating_income",
    "pretax_income",
    "income_tax_expense",
    "net_income",
    "net_income_to_common",
    "eps_basic",
    "eps_diluted",
    "shares_basic_weighted",
    "shares_diluted_weighted",
    "ebitda",
]
BS_FIELDS: list[str] = [
    "cash_and_equivalents",
    "short_term_investments",
    "accounts_receivable",
    "inventory",
    "total_current_assets",
    "ppe_net",
    "goodwill",
    "intangibles",
    "long_term_investments",
    "total_assets",
    "accounts_payable",
    "short_term_debt",
    "total_current_liabilities",
    "long_term_debt",
    "long_term_lease_liabilities",
    "total_liabilities",
    "retained_earnings",
    "minority_interest",
    "total_equity",
    "shares_outstanding",
]
CF_FIELDS: list[str] = [
    "cfo",
    "capex",
    "acquisitions",
    "cfi",
    "dividends_paid",
    "share_repurchases",
    "share_issuance",
    "debt_issued",
    "debt_repaid",
    "cff",
    "fx_effect",
    "net_change_in_cash",
    "fcf",
]
ALL_FIELDS: list[str] = IS_FIELDS + BS_FIELDS + CF_FIELDS
STATEMENT_OF: dict[str, str] = {
    **{f: "IS" for f in IS_FIELDS},
    **{f: "BS" for f in BS_FIELDS},
    **{f: "CF" for f in CF_FIELDS},
}

# flow: sums over time (TTM = sum of 4 quarters). stock: instant (TTM = latest). per_share: summed like a flow.
# avg: weighted-average share counts (TTM = mean of 4 quarters; Q(n) = n*YTD(n) - (n-1)*YTD(n-1)).
FIELD_KIND: dict[str, str] = {f: "flow" for f in IS_FIELDS + CF_FIELDS}
FIELD_KIND.update({f: "stock" for f in BS_FIELDS})
FIELD_KIND.update({"eps_basic": "per_share", "eps_diluted": "per_share"})
FIELD_KIND.update({"shares_basic_weighted": "avg", "shares_diluted_weighted": "avg"})

# Fields stored as positive magnitudes even though statements print them in parentheses.
POSITIVE_MAGNITUDE: frozenset[str] = frozenset(
    {
        "cost_of_revenue",
        "rnd_expense",
        "sga_expense",
        "other_operating_expense",
        "depreciation_amortization",
        "stock_based_comp",
        "interest_expense",
        "income_tax_expense",
        "capex",
        "acquisitions",
        "dividends_paid",
        "share_repurchases",
        "debt_repaid",
    }
)

# Derived fallbacks: field -> (formula label, inputs, fn). Only used when the field itself was not reported.
DERIVED: dict[str, tuple[str, tuple[str, ...]]] = {
    "gross_profit": ("revenue - cost_of_revenue", ("revenue", "cost_of_revenue")),
    "total_liabilities": ("total_assets - total_equity", ("total_assets", "total_equity")),
    "ebitda": (
        "operating_income + depreciation_amortization",
        ("operating_income", "depreciation_amortization"),
    ),
    "fcf": ("cfo - capex", ("cfo", "capex")),
}


def derive_value(fld: str, vals: dict[str, float | None]) -> float | None:
    """Evaluate one derived fallback over resolved values (None when an input is missing)."""
    if fld == "gross_profit":
        r, c = vals.get("revenue"), vals.get("cost_of_revenue")
        return None if r is None or c is None else r - c
    if fld == "total_liabilities":
        a, e = vals.get("total_assets"), vals.get("total_equity")
        if a is None or e is None:
            return None
        mi = vals.get("minority_interest") or 0.0
        # total_equity may already include NCI (priority-1 concept); guard against double counting.
        return a - e - (mi if vals.get("_equity_excludes_nci") else 0.0)
    if fld == "ebitda":
        o, d = vals.get("operating_income"), vals.get("depreciation_amortization")
        return None if o is None or d is None else o + d
    if fld == "fcf":
        o, c = vals.get("cfo"), vals.get("capex")
        return None if o is None or c is None else o - c
    return None


PERIOD_TYPES = ("FY", "Q", "H", "TTM")
SOURCES = ("edgar_xbrl", "xbrl_org", "6k_parsed", "6k_llm", "pdf_llm", "manual", "derived")


@dataclass
class FactRow:
    """One canonical number with provenance. Mirrors the `facts` table (see models.py)."""

    entity_id: str
    ticker: str | None
    canonical_field: str
    period_end: date
    period_type: str  # FY | Q | H | TTM
    value: float
    source: str
    fiscal_year: int | None = None
    fiscal_period: str | None = None  # FY, Q1..Q4, H1, H2, TTM
    period_start: date | None = None
    currency: str = "USD"
    unit_scale: int = 0
    source_concept: str | None = None
    accession_or_url: str | None = None
    page: int | None = None
    filed_at: date | None = None
    restated_flag: bool = False
    confidence: float = 1.0
    approved: bool = True
    doc_id: int | None = None
    schema_version: int = SCHEMA_VERSION
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> tuple:
        return (
            self.entity_id,
            self.canonical_field,
            self.period_end,
            self.period_type,
            self.source,
            self.restated_flag,
        )
