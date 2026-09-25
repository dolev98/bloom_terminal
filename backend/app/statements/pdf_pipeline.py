"""Maya (TASE) PDF pipeline: locate statement pages by Hebrew/English headings, trim the PDF, extract with Claude
into the strict schema, validate. Everything except the LLM call is pure; the call is injectable (`parse_fn`).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from pathlib import Path

from app.statements.fields import FIELD_KIND, POSITIVE_MAGNITUDE, FactRow
from app.statements.periods import approx_start
from app.statements.schemas import PDF_SYSTEM, StatementsExtraction

log = logging.getLogger(__name__)

SOURCE = "pdf_llm"
MODEL = "claude-sonnet-5"
MAX_PAGES = 14

# Hebrew statement titles, tolerant of qualifiers (מאוחד/מאוחדים/ביניים/תמציתיים) between the words.
HEADINGS: dict[str, tuple[str, ...]] = {
    "BS": (
        r"דוח(?:ות)?(?:\s+\S+){0,3}\s+על\s+ה?מצב\s+ה?כספי",
        r"מאזן(?:ים)?(?:\s+מאוחד(?:ים)?)?\b",
        r"statements? of financial position",
        r"balance sheets?",
    ),
    "IS": (
        r"דוח(?:ות)?(?:\s+\S+){0,3}\s+(?:רווח\s+והפסד|רווח\s+או\s+הפסד|על\s+ה?רווח\s+(?:ה?כולל|או\s+ה?הפסד|והפסד))",
        r"דוח(?:ות)?\s+רווח\s+והפסד",
        r"statements? of profit or loss",
        r"statements? of comprehensive income",
        r"income statements?",
        r"statements? of operations",
    ),
    "CF": (
        r"דוח(?:ות)?(?:\s+\S+){0,3}\s+על\s+תזרימי\s+ה?מזומנים",
        r"דוח(?:ות)?\s+תזרימי\s+מזומנים",
        r"statements? of cash flows?",
    ),
}
HEADING_RES: dict[str, tuple[re.Pattern[str], ...]] = {
    k: tuple(re.compile(x) for x in v) for k, v in HEADINGS.items()
}
UNIT_NOTES: tuple[tuple[str, int, str | None], ...] = (
    ('באלפי ש"ח', 3, "ILS"),
    ('אלפי ש"ח', 3, "ILS"),
    ('במיליוני ש"ח', 6, "ILS"),
    ('מיליוני ש"ח', 6, "ILS"),
    ("באלפי שקלים", 3, "ILS"),
    ("במיליוני שקלים", 6, "ILS"),
    ("באלפי דולר", 3, "USD"),
    ("אלפי דולר", 3, "USD"),
    ("במיליוני דולר", 6, "USD"),
    ("nis in thousands", 3, "ILS"),
    ("nis in millions", 6, "ILS"),
    ("u.s. dollars in thousands", 3, "USD"),
    ("usd in thousands", 3, "USD"),
    ("in thousands", 3, None),
    ("in millions", 6, None),
)
TOC_MARKERS = ("תוכן עניינים", "תוכן העניינים", "table of contents")
NUM_TOKEN_RE = re.compile(r"\(?\d{1,3}(?:,\d{3})+\)?|\(?\d{4,}\)?")
MIN_NUMERIC_DENSITY = 12


def _norm(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "")
    s = s.replace("״", '"').replace("''", '"').replace("׳", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[֑-ׇ]", "", s)  # nikud / cantillation
    s = re.sub(r"[ \t]+", " ", s)
    return s.lower()


def _variants(text: str) -> list[str]:
    """Normalised text plus a per-line reversed copy (PDF extractors often emit Hebrew in visual order)."""
    n = _norm(text)
    rev = "\n".join(line[::-1] for line in n.split("\n"))
    return [n, rev]


def numeric_density(text: str) -> int:
    return len(NUM_TOKEN_RE.findall(text or ""))


def page_texts(path: Path | str) -> list[str]:
    path = Path(path)
    try:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            return [(p.extract_text() or "") for p in pdf.pages]
    except Exception as e:  # pragma: no cover - fallback path
        log.info("pdfplumber failed (%s); falling back to pypdf", e)
    from pypdf import PdfReader

    return [(p.extract_text() or "") for p in PdfReader(str(path)).pages]


@dataclass
class Located:
    pages: list[int] = field(default_factory=list)  # 0-based indexes into the original PDF
    kinds: dict[int, list[str]] = field(default_factory=dict)
    unit_scale: int | None = None
    currency: str | None = None
    toc_pages: list[int] = field(default_factory=list)


def detect_units(text: str) -> tuple[int | None, str | None]:
    for variant in _variants(text):
        for note, scale, cur in UNIT_NOTES:
            if note in variant:
                return scale, cur
    return None, None


def page_kinds(text: str) -> list[str]:
    found: list[str] = []
    for kind, patterns in HEADING_RES.items():
        for variant in _variants(text):
            if any(rx.search(variant) for rx in patterns):
                found.append(kind)
                break
    return found


def locate_statement_pages(texts: list[str], max_pages: int = MAX_PAGES) -> Located:
    loc = Located()
    for i, t in enumerate(texts):
        variants = _variants(t)
        if any(m in v for v in variants for m in TOC_MARKERS):
            loc.toc_pages.append(i)
            continue
        kinds = page_kinds(t)
        if not kinds:
            continue
        if len(kinds) >= 3 and numeric_density(t) < MIN_NUMERIC_DENSITY:
            loc.toc_pages.append(i)  # auditor's report / contents listing all statements
            continue
        if numeric_density(t) < MIN_NUMERIC_DENSITY:
            continue
        loc.kinds[i] = kinds
        if loc.unit_scale is None:
            loc.unit_scale, loc.currency = detect_units(t)
    pages: list[int] = []
    for i in sorted(loc.kinds):
        for j in (i, i + 1):
            if (
                j < len(texts)
                and j not in pages
                and (j == i or (numeric_density(texts[j]) >= MIN_NUMERIC_DENSITY and j not in loc.kinds))
            ):
                pages.append(j)
    loc.pages = sorted(pages)[:max_pages]
    if loc.unit_scale is None:
        for i in loc.pages:
            loc.unit_scale, loc.currency = detect_units(texts[i])
            if loc.unit_scale is not None:
                break
    return loc


def build_trimmed_pdf(path: Path | str, pages: list[int]) -> bytes:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(path))
    writer = PdfWriter()
    for i in pages:
        if 0 <= i < len(reader.pages):
            writer.add_page(reader.pages[i])
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def statement_instructions(
    ticker: str, fiscal_year: int | None, period_type: str | None, located: Located
) -> str:
    kinds = sorted({k for ks in located.kinds.values() for k in ks})
    hint = (
        f"Detected unit note: 10^{located.unit_scale} {located.currency or ''}. "
        if located.unit_scale is not None
        else ""
    )
    return (
        f"Company ticker: {ticker}. Expected fiscal year {fiscal_year or 'unknown'}, period type {period_type or 'unknown'}. "
        f"The document contains {len(located.pages)} selected pages; statements detected: {', '.join(kinds) or 'unknown'}. {hint}"
        "Extract every period column of the balance sheet, income statement and cash-flow statement. "
        "Page numbers in `pages` refer to this trimmed document (1 = first page)."
    )


async def extract(
    path: Path | str,
    *,
    ticker: str,
    fiscal_year: int | None = None,
    period_type: str | None = None,
    parse_fn=None,
    texts: list[str] | None = None,
    model: str = MODEL,
) -> dict:
    """Locate -> trim -> Claude parse. Returns {extraction, usage, located, page_map, pdf_pages}."""
    from app.llm.client import pdf_block, text_block

    path = Path(path)
    texts = texts if texts is not None else page_texts(path)
    located = locate_statement_pages(texts)
    pages = located.pages or list(range(min(len(texts), MAX_PAGES)))
    pdf_bytes = build_trimmed_pdf(path, pages)
    page_map = {i + 1: p + 1 for i, p in enumerate(pages)}  # trimmed (1-based) -> original (1-based)
    if parse_fn is None:
        from app.llm import client as llm

        parse_fn = llm.parse
    extraction, usage = await parse_fn(
        StatementsExtraction,
        PDF_SYSTEM,
        [
            pdf_block(pdf_bytes, title=path.name, citations=False),
            text_block(statement_instructions(ticker, fiscal_year, period_type, located)),
        ],
        model=model,
        purpose="statements_pdf",
        max_tokens=16000,
        estimated_usd=0.3,
    )
    return {
        "extraction": extraction,
        "usage": usage,
        "located": located,
        "page_map": page_map,
        "pdf_pages": len(pages),
    }


def scale_value(fld: str, v: float, unit_scale: int) -> float:
    kind = FIELD_KIND.get(fld, "flow")
    if kind == "per_share":
        out = v
    elif kind == "avg" or fld == "shares_outstanding":
        out = v * 10**unit_scale if abs(v) < 1e8 else v
    else:
        out = v * 10**unit_scale
    return abs(out) if fld in POSITIVE_MAGNITUDE else out


def extraction_periods(extraction: StatementsExtraction) -> list[dict]:
    """[{period_end, period_type, fiscal_year, fiscal_period, values(base units)}] for validation / storage."""
    out = []
    for p in extraction.periods:
        vals = {
            k: scale_value(k, v, extraction.unit_scale)
            for k, v in p.values.model_dump().items()
            if v is not None
        }
        out.append(
            {
                "period_end": p.period_end,
                "period_type": p.period_type,
                "fiscal_year": p.fiscal_year,
                "fiscal_period": p.fiscal_period,
                "pages": p.pages,
                "values": vals,
            }
        )
    return out


def extraction_to_facts(
    extraction: StatementsExtraction,
    *,
    entity_id: str,
    ticker: str | None,
    source: str = SOURCE,
    path_or_url: str,
    doc_id: int | None,
    page_map: dict[int, int] | None = None,
    confidence: float | None = None,
    filed_at: date | None = None,
) -> list[FactRow]:
    conf = confidence if confidence is not None else max(0.05, min(0.95, float(extraction.confidence)))
    currency = extraction.currency if extraction.currency != "other" else ""
    rows: list[FactRow] = []
    seen: set[tuple[str, str, date]] = set()
    for p in extraction_periods(extraction):
        try:
            pend = date.fromisoformat(p["period_end"])
        except ValueError:
            continue
        months = {"FY": 12, "H": 6, "Q": 3}[p["period_type"]]
        page = None
        if p["pages"]:
            first = min(p["pages"])
            page = (page_map or {}).get(first, first)
        for fld, v in p["values"].items():
            key = (fld, p["period_type"], pend)
            if key in seen:
                continue
            seen.add(key)
            kind = FIELD_KIND.get(fld, "flow")
            rows.append(
                FactRow(
                    entity_id=entity_id,
                    ticker=ticker,
                    canonical_field=fld,
                    period_end=pend,
                    period_type=p["period_type"],
                    period_start=None if kind == "stock" else approx_start(pend, months),
                    fiscal_year=p["fiscal_year"],
                    fiscal_period=p["fiscal_period"],
                    value=v,
                    currency="" if kind == "avg" or fld == "shares_outstanding" else currency,
                    unit_scale=0 if kind == "per_share" else extraction.unit_scale,
                    source=source,
                    source_concept=f"llm:{fld}",
                    accession_or_url=path_or_url,
                    page=page,
                    filed_at=filed_at,
                    restated_flag=False,
                    confidence=conf,
                    approved=False,
                    doc_id=doc_id,
                )
            )
    return rows
