"""Versioned concept map: XBRL concepts (us-gaap / ifrs-full / dei) and statement row labels -> canonical fields.

Seeded idempotently from concept_map_seed.csv + ifrs_map_seed.csv + LABEL_SYNONYMS on first use. `ConceptMapper`
is a pure in-memory view (built from DB rows or straight from the CSVs in tests).
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from app.data.store.sqlite import session_scope
from app.statements.models import ConceptMap

HERE = Path(__file__).resolve().parent
SEED_FILES = (HERE / "concept_map_seed.csv", HERE / "ifrs_map_seed.csv")
LABEL_TAXONOMY = "label"

# (statement kind, regex on normalized label, canonical field, priority, sign). Kind "*" = any statement.
# Labels are normalized by `normalize_label` (lowercase, parentheses removed, punctuation stripped).
# Fields starting with "_" are auxiliary (combined by the parser, never stored).
LABEL_SYNONYMS: list[tuple[str, str, str, int, int]] = [
    ("IS", r"^(total )?(net )?(revenues?|sales|net sales)( net)?$", "revenue", 1, 1),
    ("IS", r"^(total )?revenues?( from .*)?$", "revenue", 2, 1),
    ("IS", r"^(total )?cost of (revenues?|sales|goods sold|services)", "cost_of_revenue", 1, 1),
    ("IS", r"^gross (profit|margin)$", "gross_profit", 1, 1),
    ("IS", r"^research and development", "rnd_expense", 1, 1),
    ("IS", r"^selling general and administrative", "sga_expense", 1, 1),
    (
        "IS",
        r"^(sales and marketing|selling and marketing|marketing and selling|selling expenses?)",
        "_selling_marketing",
        1,
        1,
    ),
    ("IS", r"^general and administrative", "_general_admin", 1, 1),
    (
        "IS",
        r"^(other operating (expenses?|income|charges)|restructuring|impairment)",
        "other_operating_expense",
        1,
        1,
    ),
    ("IS", r"^(operating (income|profit)|(income|profit) from operations)$", "operating_income", 1, 1),
    (
        "IS",
        r"^(operating (income|profit)|(income|profit|earnings) from operations)",
        "operating_income",
        2,
        1,
    ),
    (
        "IS",
        r"^(interest expenses?|financial expenses?|finance (expenses?|costs?))$",
        "interest_expense",
        1,
        1,
    ),
    ("IS", r"^(interest income|financial income|finance income)$", "interest_income", 1, 1),
    (
        "IS",
        r"^(financial|finance|interest and other|other) (income|expenses?)( and other)?( net)?$",
        "other_nonoperating_income",
        3,
        1,
    ),
    (
        "IS",
        r"^(financial|finance|other|interest and other) (expenses?|costs?) net$",
        "other_nonoperating_income",
        1,
        -1,
    ),
    (
        "IS",
        r"^(financial|finance|other|interest and other) (income|income and expenses?|income and other)( net)?$",
        "other_nonoperating_income",
        2,
        1,
    ),
    ("IS", r"^(income|profit|earnings) before (income )?tax", "pretax_income", 1, 1),
    ("IS", r"^(income )?tax(es)? (on income|expenses?|benefit|provision)", "income_tax_expense", 1, 1),
    ("IS", r"^(provision for (income )?taxes|income taxes|taxes on income)$", "income_tax_expense", 2, 1),
    ("IS", r"^net (income|profit|earnings|loss)( for the (year|period))?$", "net_income", 1, 1),
    ("IS", r"^(profit|income) for the (year|period)$", "net_income", 2, 1),
    (
        "IS",
        r"^net (income|profit|earnings|loss) attributable to (the )?(company|ordinary|shareholders|stockholders|common|parent|owners|equity)",
        "net_income_to_common",
        1,
        1,
    ),
    (
        "IS",
        r"attributable to (owners|equity holders|shareholders) of (the )?(parent|company)$",
        "net_income_to_common",
        2,
        1,
    ),
    ("IS", r"^net (income|profit|earnings|loss) attributable to (?!non)", "net_income_to_common", 3, 1),
    (
        "IS",
        r"^basic (net )?(earnings|income|eps|net income|net earnings|loss)( per (ordinary |common )?share)?$",
        "eps_basic",
        1,
        1,
    ),
    ("IS", r"^(net )?(earnings|income|loss) per (ordinary |common )?share basic$", "eps_basic", 2, 1),
    (
        "IS",
        r"^diluted (net )?(earnings|income|eps|net income|net earnings|loss)( per (ordinary |common )?share)?$",
        "eps_diluted",
        1,
        1,
    ),
    ("IS", r"^(net )?(earnings|income|loss) per (ordinary |common )?share diluted$", "eps_diluted", 2, 1),
    (
        "IS",
        r"^(weighted average (number of )?(ordinary |common )?shares.*basic|basic weighted average)",
        "shares_basic_weighted",
        1,
        1,
    ),
    (
        "IS",
        r"^(weighted average (number of )?(ordinary |common )?shares.*diluted|diluted weighted average)",
        "shares_diluted_weighted",
        1,
        1,
    ),
    ("BS", r"^cash and (cash )?equivalents", "cash_and_equivalents", 1, 1),
    ("BS", r"^cash$", "cash_and_equivalents", 2, 1),
    (
        "BS",
        r"^(short term (bank )?deposits|marketable securities|short term investments|bank deposits|short term deposits and marketable securities)",
        "short_term_investments",
        1,
        1,
    ),
    ("BS", r"^(trade|accounts) (and other )?receivables?", "accounts_receivable", 1, 1),
    ("BS", r"^inventor(y|ies)", "inventory", 1, 1),
    ("BS", r"^total current assets", "total_current_assets", 1, 1),
    ("BS", r"^(property( plant)? and equipment|fixed assets)", "ppe_net", 1, 1),
    ("BS", r"^goodwill$", "goodwill", 1, 1),
    ("BS", r"^(other )?intangible assets", "intangibles", 1, 1),
    (
        "BS",
        r"^(long term (investments|deposits|marketable securities|bank deposits)|investments? in (associates|affiliates))",
        "long_term_investments",
        1,
        1,
    ),
    ("BS", r"^total assets$", "total_assets", 1, 1),
    ("BS", r"^(trade|accounts) (and other )?payables?", "accounts_payable", 1, 1),
    (
        "BS",
        r"^(short term (debt|borrowings|loans|credit|bank credit)|current maturities of|current portion of long term|short term (bank )?loans? and current maturities)",
        "short_term_debt",
        1,
        1,
    ),
    ("BS", r"^total current liabilities", "total_current_liabilities", 1, 1),
    (
        "BS",
        r"^(long term (debt|loans|borrowings)|senior notes|convertible (senior )?notes|bonds|debentures|(long term )?debt net of current)",
        "long_term_debt",
        1,
        1,
    ),
    (
        "BS",
        r"^(long term |non ?current )?(operating )?lease liabilit(y|ies)",
        "long_term_lease_liabilities",
        1,
        1,
    ),
    ("BS", r"^total liabilities$", "total_liabilities", 1, 1),
    ("BS", r"^(retained earnings|accumulated deficit)", "retained_earnings", 1, 1),
    ("BS", r"^(non ?controlling|minority) interests?", "minority_interest", 1, 1),
    ("BS", r"^total (shareholders|stockholders|share holders|stock holders)? ?equity$", "total_equity", 1, 1),
    ("BS", r"^total equity( attributable to.*)?$", "total_equity", 2, 1),
    (
        "CF",
        r"^net cash (provided by|from|generated (from|by)|used in|flows? from)( operating| operations)",
        "cfo",
        1,
        1,
    ),
    ("CF", r"^(cash flows? from operating activities|net cash from operating activities)$", "cfo", 2, 1),
    ("CF", r"^(purchases?|acquisitions?) of (property|fixed assets|equipment)", "capex", 1, 1),
    (
        "CF",
        r"^(capital expenditures?|investment in (property|fixed assets)|additions to property)",
        "capex",
        2,
        1,
    ),
    ("CF", r"^(acquisitions?|purchase) of (business|businesses|subsidiar)", "acquisitions", 1, 1),
    (
        "CF",
        r"^(payments? for (business )?acquisitions?|acquisitions? net of cash acquired|business combinations?)",
        "acquisitions",
        2,
        1,
    ),
    ("CF", r"^net cash (provided by|from|used in|flows? from)( investing)", "cfi", 1, 1),
    ("CF", r"^(dividends? paid|payment of dividends?|cash dividends? paid)", "dividends_paid", 1, 1),
    (
        "CF",
        r"^(repurchase|purchase|buyback|acquisition) of (treasury |ordinary |common |own )?(shares|stock)",
        "share_repurchases",
        1,
        1,
    ),
    ("CF", r"^(treasury (shares|stock)|share buyback)", "share_repurchases", 2, 1),
    (
        "CF",
        r"^proceeds from (the )?(issuance of|exercise of|issue of) (shares|stock|share options|stock options|options|ordinary shares|employee)",
        "share_issuance",
        1,
        1,
    ),
    (
        "CF",
        r"^(proceeds from (share|stock) options? exercised|issuance of shares|exercise of (share|stock) options)",
        "share_issuance",
        2,
        1,
    ),
    (
        "CF",
        r"^(proceeds from|receipt of|receipts of|issuance of) (issuance of )?(long term |bank |short term )?(debt|loans|borrowings|notes|senior notes|bonds|credit)",
        "debt_issued",
        1,
        1,
    ),
    (
        "CF",
        r"^(repayments? of|redemption of|payments? of) (long term |bank |short term )?(debt|loans|borrowings|notes|senior notes|bonds|debentures|credit)",
        "debt_repaid",
        1,
        1,
    ),
    ("CF", r"^net cash (provided by|from|used in|flows? from)( financing)", "cff", 1, 1),
    (
        "CF",
        r"^(effect of (exchange rate|foreign exchange)|(translation|exchange rate) (differences|adjustments?) on cash|erosion)",
        "fx_effect",
        1,
        1,
    ),
    ("CF", r"^(net )?(increase|decrease|change) in cash", "net_change_in_cash", 1, 1),
    (
        "*",
        r"^depreciation( and amortization| and amortisation|)( expenses?)?$",
        "depreciation_amortization",
        1,
        1,
    ),
    ("*", r"^depreciation and amortization", "depreciation_amortization", 2, 1),
    ("*", r"^(stock|share|equity)( |-)?based compensation", "stock_based_comp", 1, 1),
    ("*", r"^(income|profit) before taxes on income", "pretax_income", 3, 1),
]

_MONTHS_RE = re.compile(r"\((?:[^()]*)\)")


def normalize_label(label: str) -> str:
    """Lowercase; drop parenthesised qualifiers ("(loss)", "(used in)", "(Note 3)"), footnote marks, commas,
    quotes; hyphens -> spaces; collapse whitespace."""
    s = unicodedata.normalize("NFKC", label or "")
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = _MONTHS_RE.sub(" ", s)
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = s.lower()
    s = re.sub(r"[\*†‡]+", " ", s)
    s = re.sub(r"[,'\":;\.]", "", s)
    s = re.sub(r"[-–—/]", " ", s)
    s = re.sub(r"\bnote\s*\d+[a-z]?\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


@dataclass(frozen=True)
class MapRow:
    taxonomy: str
    concept: str
    field: str
    priority: int = 1
    sign: int = 1
    valid_from: date | None = None
    valid_to: date | None = None
    entity_override: str | None = None


def _parse_date(s: str | None) -> date | None:
    s = (s or "").strip()
    return date.fromisoformat(s) if s else None


def read_seed_rows(paths: tuple[Path, ...] = SEED_FILES, include_labels: bool = True) -> list[MapRow]:
    rows: list[MapRow] = []
    for p in paths:
        with p.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if not r.get("source_concept"):
                    continue
                rows.append(
                    MapRow(
                        taxonomy=r["source_taxonomy"].strip(),
                        concept=r["source_concept"].strip(),
                        field=r["canonical_field"].strip(),
                        priority=int(r.get("priority") or 1),
                        sign=int(r.get("sign") or 1),
                        valid_from=_parse_date(r.get("valid_from")),
                        valid_to=_parse_date(r.get("valid_to")),
                    )
                )
    if include_labels:
        rows.extend(
            MapRow(LABEL_TAXONOMY, f"{kind}|{rx}", fld, prio, sign)
            for kind, rx, fld, prio, sign in LABEL_SYNONYMS
        )
    return rows


class ConceptMapper:
    """In-memory lookup over concept-map rows. Entity overrides beat global rows; validity is by period end."""

    def __init__(self, rows: list[MapRow]):
        self.rows = rows
        self._by_concept: dict[tuple[str, str], list[MapRow]] = {}
        self._labels: list[tuple[str, re.Pattern[str], MapRow]] = []
        for r in rows:
            if r.taxonomy == LABEL_TAXONOMY:
                kind, _, rx = r.concept.partition("|")
                try:
                    self._labels.append((kind or "*", re.compile(rx), r))
                except re.error:
                    continue
            else:
                self._by_concept.setdefault((r.taxonomy, r.concept), []).append(r)
        self._labels.sort(key=lambda t: (t[2].priority, 0 if t[2].entity_override else 1))

    @classmethod
    def from_seed(cls) -> ConceptMapper:
        return cls(read_seed_rows())

    def taxonomies(self) -> set[str]:
        return {t for t, _ in self._by_concept}

    def lookup(
        self, taxonomy: str, concept: str, period_end: date | None = None, entity_id: str | None = None
    ) -> list[tuple[str, int, int]]:
        """[(canonical_field, priority, sign)] for a concept (a concept may feed several fields)."""
        best: dict[str, tuple[int, int]] = {}
        for r in self._by_concept.get((taxonomy, concept), ()):
            if r.entity_override and r.entity_override != entity_id:
                continue
            if period_end is not None:
                if r.valid_from and period_end < r.valid_from:
                    continue
                if r.valid_to and period_end > r.valid_to:
                    continue
            prio = r.priority - (1000 if r.entity_override else 0)
            if r.field not in best or prio < best[r.field][0]:
                best[r.field] = (prio, r.sign)
        return [(f, p, s) for f, (p, s) in best.items()]

    def match_label(self, kind: str, label: str, entity_id: str | None = None) -> tuple[str, int] | None:
        """(canonical_field, sign) for a statement row label of statement `kind` (IS|BS|CF), else None."""
        norm = normalize_label(label)
        if not norm:
            return None
        for k, rx, r in self._labels:
            if k not in ("*", kind):
                continue
            if r.entity_override and r.entity_override != entity_id:
                continue
            if rx.search(norm):
                return r.field, r.sign
        return None


# --- DB ---------------------------------------------------------------------------------------------------


async def ensure_seeded() -> int:
    """Insert seed rows that are missing (idempotent; user overrides are never touched). Returns rows added."""
    async with session_scope() as s:
        n = (await s.execute(select(func.count()).select_from(ConceptMap))).scalar_one()
        existing = set()
        if n:
            rows = (
                await s.execute(
                    select(ConceptMap.source_taxonomy, ConceptMap.source_concept, ConceptMap.canonical_field)
                )
            ).all()
            existing = {(a, b, c) for a, b, c in rows}
        added = 0
        for r in read_seed_rows():
            if (r.taxonomy, r.concept, r.field) in existing:
                continue
            s.add(
                ConceptMap(
                    source_taxonomy=r.taxonomy,
                    source_concept=r.concept,
                    canonical_field=r.field,
                    priority=r.priority,
                    sign=r.sign,
                    valid_from=r.valid_from,
                    valid_to=r.valid_to,
                    entity_override=None,
                )
            )
            added += 1
        return added


async def load_mapper() -> ConceptMapper:
    await ensure_seeded()
    async with session_scope() as s:
        rows = (await s.execute(select(ConceptMap))).scalars().all()
    return ConceptMapper(
        [
            MapRow(
                r.source_taxonomy,
                r.source_concept,
                r.canonical_field,
                r.priority,
                r.sign,
                r.valid_from,
                r.valid_to,
                r.entity_override,
            )
            for r in rows
        ]
    )


async def list_rows(taxonomy: str | None = None) -> list[dict]:
    await ensure_seeded()
    async with session_scope() as s:
        q = select(ConceptMap).order_by(ConceptMap.canonical_field, ConceptMap.priority)
        if taxonomy:
            q = q.where(ConceptMap.source_taxonomy == taxonomy)
        rows = (await s.execute(q)).scalars().all()
    return [_dump(r) for r in rows]


async def upsert_row(
    taxonomy: str,
    concept: str,
    field: str,
    priority: int = 1,
    sign: int = 1,
    valid_from: date | None = None,
    valid_to: date | None = None,
    entity_override: str | None = None,
) -> dict:
    await ensure_seeded()
    async with session_scope() as s:
        q = select(ConceptMap).where(
            ConceptMap.source_taxonomy == taxonomy,
            ConceptMap.source_concept == concept,
            ConceptMap.canonical_field == field,
            ConceptMap.entity_override.is_(None)
            if entity_override is None
            else ConceptMap.entity_override == entity_override,
        )
        row = (await s.execute(q)).scalar_one_or_none()
        if row is None:
            row = ConceptMap(
                source_taxonomy=taxonomy,
                source_concept=concept,
                canonical_field=field,
                entity_override=entity_override,
            )
            s.add(row)
        row.priority = priority
        row.sign = sign
        row.valid_from = valid_from
        row.valid_to = valid_to
        await s.flush()
        return _dump(row)


def _dump(r: ConceptMap) -> dict:
    return {
        "id": r.id,
        "source_taxonomy": r.source_taxonomy,
        "source_concept": r.source_concept,
        "canonical_field": r.canonical_field,
        "priority": r.priority,
        "sign": r.sign,
        "valid_from": r.valid_from,
        "valid_to": r.valid_to,
        "entity_override": r.entity_override,
    }
