"""Strict Pydantic schemas for Claude extraction (PDF and 6-K fallback) and the shared system prompts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, create_model

from app.statements.fields import ALL_FIELDS, BS_FIELDS, CF_FIELDS, IS_FIELDS

EXTRACT_FIELDS = [f for f in ALL_FIELDS if f not in ("ebitda", "fcf")]

CanonicalValues = create_model(  # type: ignore[call-overload]
    "CanonicalValues", **{f: (float | None, Field(default=None)) for f in EXTRACT_FIELDS}
)


class ExtractedPeriod(BaseModel):
    period_end: str = Field(description="Period end / balance-sheet date as YYYY-MM-DD")
    period_type: Literal["FY", "Q", "H"]
    fiscal_year: int
    fiscal_period: Literal["FY", "Q1", "Q2", "Q3", "Q4", "H1", "H2"]
    is_comparative: bool = Field(default=False, description="True for prior-period comparative columns")
    pages: list[int] = Field(
        default_factory=list,
        description="1-based page numbers (of the supplied document) the values were read from",
    )
    values: CanonicalValues  # type: ignore[valid-type]


class StatementsExtraction(BaseModel):
    company_name: str | None = None
    currency: Literal["ILS", "USD", "EUR", "GBP", "other"] = "ILS"
    unit_scale: int = Field(
        description="Power of ten the printed numbers are expressed in: 3 = thousands, 6 = millions, 0 = units"
    )
    periods: list[ExtractedPeriod]
    confidence: float = Field(ge=0.0, le=1.0, description="Overall extraction confidence 0..1")
    notes: str | None = Field(default=None, description="Ambiguities, missing statements, sign conventions")


def _field_guide() -> str:
    return "\n".join(
        [
            "Income statement fields: " + ", ".join(f for f in IS_FIELDS if f != "ebitda"),
            "Balance sheet fields: " + ", ".join(BS_FIELDS),
            "Cash flow fields: " + ", ".join(f for f in CF_FIELDS if f != "fcf"),
        ]
    )


COMMON_RULES = f"""You extract financial statements into a fixed canonical schema. Rules:
- Report numbers exactly as printed (do not rescale); set unit_scale to the document's stated unit (3 for thousands, 6 for millions).
- Expenses (cost_of_revenue, rnd_expense, sga_expense, income_tax_expense, interest_expense, depreciation_amortization,
  stock_based_comp) and cash outflows (capex, acquisitions, dividends_paid, share_repurchases, debt_repaid) are POSITIVE magnitudes.
- Losses / negative cash flows (net_income, operating_income, cfo, cfi, cff, net_change_in_cash, fx_effect) keep their sign.
- eps_* are per-share amounts, never scaled. shares_* are counts (state in notes if printed in thousands).
- Leave a field null when it is not reported; never invent or derive values (do not compute gross_profit or totals yourself).
- One entry per period column, including comparatives (is_comparative=true). Balance-sheet dates map to the period whose end they are.
- period_type: FY for annual, Q for a three-month period, H for a six-month period. Use fiscal_period Q1..Q4 / H1 / H2 / FY.
{_field_guide()}"""

PDF_SYSTEM = (
    "You are a meticulous Israeli CPA reading Hebrew (or English) financial statements published on the TASE (Maya). "
    "Hebrew headings: 'דוח על המצב הכספי' = balance sheet, 'דוח רווח והפסד' / 'דוח על הרווח הכולל' = income statement, "
    "'דוח על תזרימי המזומנים' = cash flow statement. 'באלפי ש\"ח' = NIS thousands (unit_scale 3, currency ILS), "
    "'במיליוני ש\"ח' = NIS millions. Numbers in parentheses are negative. Dates are day/month/year.\n"
    + COMMON_RULES
)

SIXK_SYSTEM = (
    "You read an SEC Form 6-K exhibit (earnings press release of a foreign private issuer) and extract the GAAP "
    "consolidated statements (not the non-GAAP reconciliations). Column headers give the period lengths "
    "('Three months ended' = Q, 'Six months ended' = H, 'Year ended' = FY).\n" + COMMON_RULES
)
