"""6-K exhibit (EX-99.x earnings release) parser: HTML statement tables -> canonical FactRows (source 6k_parsed).

Pure parts: `find_statement_tables(html)`, `tables_to_facts(...)`, `html_text(html)`. Network part:
`fetch_exhibits(cik, accession)` (sec.gov Archives, rate-limited through the EDGAR provider).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from bs4 import BeautifulSoup, NavigableString, Tag

from app.statements.concept_map import ConceptMapper, normalize_label
from app.statements.fields import FIELD_KIND, POSITIVE_MAGNITUDE, FactRow
from app.statements.periods import approx_start, fiscal_year_of, half_of, is_fye, quarter_of

log = logging.getLogger(__name__)

SOURCE = "6k_parsed"
CONFIDENCE = 0.7
ARCHIVE_INDEX = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/index.json"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/"

KIND_PATTERNS = {
    "IS": re.compile(
        r"statements? of (operations|income|earnings|profit or loss|profit and loss|comprehensive (income|loss))|income statements?|statements? of profit",
        re.I,
    ),
    "BS": re.compile(r"balance sheets?|statements? of financial position", re.I),
    "CF": re.compile(r"statements? of cash flows?|cash flows? statements?", re.I),
}
UNIT_PATTERNS = [
    (
        re.compile(
            r"in thousands|thousands of|\(thousands\)|\b000s\b|in nis thousands|nis in thousands", re.I
        ),
        3,
    ),
    (re.compile(r"in millions|millions of|\(millions\)|in nis millions|nis in millions", re.I), 6),
    (re.compile(r"in billions|billions of", re.I), 9),
]
CURRENCY_PATTERNS = [
    (re.compile(r"u\.?s\.? ?dollars?|usd|\bus\$|\$", re.I), "USD"),
    (re.compile(r"\bnis\b|shekel|₪|\bils\b", re.I), "ILS"),
    (re.compile(r"\beuros?\b|\beur\b|€", re.I), "EUR"),
]
MONTHS = {
    m: i + 1
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ]
    )
}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
MONTH_DAY_RE = re.compile(rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})\b|\b(\d{{1,2}})\s+({_MONTH_ALT})\b", re.I)
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
DURATION_WORDS = [
    (re.compile(r"three months|3 months|quarter ended|quarterly|three month", re.I), 3),
    (re.compile(r"six months|6 months|half[- ]year|six month", re.I), 6),
    (re.compile(r"nine months|9 months|nine month", re.I), 9),
    (
        re.compile(
            r"twelve months|12 months|years? ended|fiscal year|year ending|full year|twelve month|year$", re.I
        ),
        12,
    ),
]
NUM_RE = re.compile(r"^\(?-?[\d,]+(\.\d+)?\)?$")
DASHES = {"—", "–", "-", "—-", "- -", "–-"}


@dataclass
class PeriodCol:
    cols: list[int]
    end: date
    months: int | None  # None = instant (balance sheet)


@dataclass
class StatementTable:
    kind: str
    unit_scale: int
    currency: str
    grid: list[list[str]]
    heading: str
    periods: list[PeriodCol]
    header_rows: int
    shares_scaled: bool = True
    rows: list[tuple[str, list[float | None]]] = field(default_factory=list)


def _clean(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def parse_number(cell: str) -> float | None:
    """'(1,234)' -> -1234.0; '1,234.5' -> 1234.5; '—' -> 0.0; '$' / 'Note 3a' / '' -> None."""
    s = _clean(cell).replace("$", "").replace("€", "").replace("₪", "").replace("NIS", "").strip()
    if not s:
        return None
    if s in DASHES:
        return 0.0
    if s.endswith("%"):
        return None
    neg = s.startswith("(") or s.startswith("-") and len(s) > 1
    s2 = s.strip("()").lstrip("-").replace(",", "").strip()
    if not s2 or not re.fullmatch(r"\d+(\.\d+)?", s2):
        return None
    v = float(s2)
    return -v if neg else v


def table_grid(table: Tag) -> list[list[str]]:
    grid: list[list[str]] = []
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue
        row: list[str] = []
        for cell in tr.find_all(["td", "th"], recursive=False):
            text = _clean(cell.get_text(" ", strip=True))
            try:
                span = max(1, int(cell.get("colspan", 1)))
            except (TypeError, ValueError):
                span = 1
            row.extend([text] * span)
        if any(row):
            grid.append(row)
    return grid


def _context_text(table: Tag, max_strings: int = 80) -> str:
    """Text preceding the table (up to the previous table), in document order."""
    parts: list[str] = []
    for el in table.previous_elements:
        if isinstance(el, Tag) and el.name == "table":
            break
        if isinstance(el, NavigableString):
            if el.parent and el.parent.name in ("script", "style"):
                continue
            t = _clean(str(el))
            if t:
                parts.append(t)
                if len(parts) >= max_strings:
                    break
    return " ".join(reversed(parts))


def _last_match(text: str, patterns: list[tuple[re.Pattern[str], int]]) -> int | None:
    best: tuple[int, int] | None = None
    for rx, val in patterns:
        for m in rx.finditer(text):
            if best is None or m.end() > best[0]:
                best = (m.end(), val)
    return best[1] if best else None


def detect_kind(text: str) -> str | None:
    best: tuple[int, str] | None = None
    for kind, rx in KIND_PATTERNS.items():
        for m in rx.finditer(text):
            if best is None or m.end() > best[0]:
                best = (m.end(), kind)
    return best[1] if best else None


def detect_unit_scale(text: str) -> int | None:
    return _last_match(text, UNIT_PATTERNS)


def detect_currency(text: str) -> str | None:
    for rx, cur in CURRENCY_PATTERNS:
        if rx.search(text):
            return cur
    return None


def _is_data_row(row: list[str]) -> bool:
    if not row or not row[0] or parse_number(row[0]) is not None or YEAR_RE.fullmatch(row[0].strip()):
        return False
    return any(parse_number(c) not in (None, 0.0) for c in row[1:])


def _header_rows(grid: list[list[str]]) -> int:
    for i, row in enumerate(grid):
        if _is_data_row(row):
            return i
    return len(grid)


def _month_day(text: str) -> tuple[int, int] | None:
    m = MONTH_DAY_RE.search(text)
    if not m:
        return None
    if m.group(1):
        return MONTHS[m.group(1).lower()[:3]], int(m.group(2))
    return MONTHS[m.group(4).lower()[:3]], int(m.group(3))


def _safe_date(y: int, m: int, d: int) -> date:
    while d > 28:
        try:
            return date(y, m, d)
        except ValueError:
            d -= 1
    return date(y, m, d)


def find_periods(grid: list[list[str]], n_header: int) -> list[PeriodCol]:
    if n_header == 0:
        return []
    ncols = max(len(r) for r in grid)
    all_header = " ".join(" ".join(grid[r]) for r in range(n_header))
    global_md = _month_day(all_header)
    global_months = _last_match(all_header, DURATION_WORDS)
    found: list[PeriodCol] = []
    for c in range(1, ncols):
        text = " ".join(grid[r][c] for r in range(n_header) if c < len(grid[r]) and grid[r][c])
        years = YEAR_RE.findall(text)
        if not years or "%" in text or re.search(r"\bchange\b", text, re.I):
            continue
        year = int(years[-1])
        md = _month_day(text) or global_md
        if md is None:
            continue
        months = _last_match(text, DURATION_WORDS)
        if months is None:
            months = global_months
        end = _safe_date(year, md[0], md[1])
        if found and found[-1].end == end and found[-1].months == months and found[-1].cols[-1] == c - 1:
            found[-1].cols.append(c)
            continue
        if any(p.end == end and p.months == months for p in found):
            continue
        found.append(PeriodCol([c], end, months))
    return found


def extract_rows(st: StatementTable) -> list[tuple[str, list[float | None]]]:
    rows: list[tuple[str, list[float | None]]] = []
    claimed = {c for p in st.periods for c in p.cols}
    for row in st.grid[st.header_rows :]:
        label = row[0] if row else ""
        if not label:
            continue
        vals: list[float | None] = []
        for p in st.periods:
            v: float | None = None
            for c in p.cols:
                if c < len(row):
                    v = parse_number(row[c])
                    if v is not None:
                        break
            if v is None:
                nxt = p.cols[-1] + 1
                if nxt not in claimed and nxt < len(row):
                    v = parse_number(row[nxt])
            vals.append(v)
        if all(v is None for v in vals):
            nums = [parse_number(c) for c in row[1:]]
            nums = [n for n in nums if n is not None]
            if len(nums) == len(st.periods) and nums:
                vals = nums  # sequential fallback (mis-aligned colspans)
        rows.append((label, vals))
    return rows


def find_statement_tables(html: str) -> list[StatementTable]:
    soup = BeautifulSoup(html, "lxml")
    out: list[StatementTable] = []
    for table in soup.find_all("table"):
        grid = table_grid(table)
        if len(grid) < 3:
            continue
        n_header = _header_rows(grid)
        header_text = " ".join(" ".join(r) for r in grid[:n_header])
        context = _context_text(table)
        kind = detect_kind(header_text) or detect_kind(context)
        if kind is None:
            continue
        periods = find_periods(grid, n_header)
        if not periods:
            continue
        if kind == "BS":
            for p in periods:
                p.months = None
        unit_text = header_text + " " + context
        scale = detect_unit_scale(header_text)
        if scale is None:
            scale = detect_unit_scale(context)
        currency = detect_currency(header_text) or detect_currency(context) or "USD"
        shares_scaled = not re.search(
            r"except (per share and )?share|except share|share and per share data|share amounts",
            unit_text,
            re.I,
        )
        st = StatementTable(
            kind, scale or 0, currency, grid, header_text[:300], periods, n_header, shares_scaled
        )
        st.rows = extract_rows(st)
        if sum(1 for _, vals in st.rows if any(v is not None for v in vals)) < 3:
            continue
        out.append(st)
    return out


def _classify(kind: str, p: PeriodCol, fye_month: int) -> list[tuple[str, int, str, date | None]]:
    """[(period_type, fiscal_year, fiscal_period, period_start)] rows a column maps to."""
    fy = fiscal_year_of(p.end, fye_month)
    if p.months is None:
        rows = [("Q", fy, f"Q{quarter_of(p.end, fye_month)}", None)]
        if is_fye(p.end, fye_month):
            rows.append(("FY", fy, "FY", None))
        return rows
    if p.months == 3:
        return [("Q", fy, f"Q{quarter_of(p.end, fye_month)}", approx_start(p.end, 3))]
    if p.months == 6:
        return [("H", fy, f"H{half_of(p.end, fye_month)}", approx_start(p.end, 6))]
    if p.months == 12 and is_fye(p.end, fye_month):
        return [("FY", fy, "FY", approx_start(p.end, 12))]
    return []


def _resolve_basic_diluted(norm: str, section: str, value: float | None) -> str | None:
    if norm not in ("basic", "diluted"):
        return None
    sec = normalize_label(section)
    if ("share" in sec and "per" not in sec) or (value is not None and abs(value) > 10000):
        return f"shares_{norm}_weighted"
    return f"eps_{norm}"


def tables_to_facts(
    tables: list[StatementTable],
    mapper: ConceptMapper,
    *,
    entity_id: str,
    ticker: str | None,
    fye_month: int,
    accession: str | None,
    filed_at: date | None,
    source: str = SOURCE,
    confidence: float = CONFIDENCE,
    doc_id: int | None = None,
) -> tuple[list[FactRow], dict]:
    facts: dict[tuple[str, str, date], FactRow] = {}
    aux: dict[tuple[str, date], dict[str, float]] = {}
    unmatched: list[str] = []
    meta_tables = []
    for st in sorted(tables, key=lambda t: {"IS": 0, "BS": 1, "CF": 2}[t.kind]):
        matched = 0
        section = ""
        for label, vals in st.rows:
            if all(v is None for v in vals):
                section = label
                continue
            norm = normalize_label(label)
            fld_sign = mapper.match_label(st.kind, label, entity_id)
            special = _resolve_basic_diluted(norm, section, next((v for v in vals if v is not None), None))
            if special:
                fld_sign = (special, 1)
            if fld_sign is None:
                unmatched.append(label)
                continue
            fld, sign = fld_sign
            matched += 1
            kind = FIELD_KIND.get(fld, "flow")
            for p, raw in zip(st.periods, vals, strict=False):
                if raw is None:
                    continue
                v = raw * sign
                if kind == "per_share":
                    pass
                elif kind in ("avg",) or fld == "shares_outstanding":
                    if st.shares_scaled and abs(v) < 1e8:
                        v *= 10**st.unit_scale
                else:
                    v *= 10**st.unit_scale
                if fld in POSITIVE_MAGNITUDE:
                    v = abs(v)
                for ptype, fy, fp, pstart in _classify(st.kind, p, fye_month):
                    if fld.startswith("_"):
                        aux.setdefault((ptype, p.end), {})[fld] = v
                        continue
                    key = (fld, ptype, p.end)
                    if key in facts:
                        continue
                    facts[key] = FactRow(
                        entity_id=entity_id,
                        ticker=ticker,
                        canonical_field=fld,
                        period_end=p.end,
                        period_type=ptype,
                        period_start=pstart,
                        fiscal_year=fy,
                        fiscal_period=fp,
                        value=v,
                        currency="" if kind in ("avg",) or fld == "shares_outstanding" else st.currency,
                        unit_scale=0 if kind == "per_share" else st.unit_scale,
                        source=source,
                        source_concept=f"label:{label}",
                        accession_or_url=accession,
                        filed_at=filed_at,
                        confidence=confidence,
                        approved=False,
                        doc_id=doc_id,
                    )
        meta_tables.append(
            {
                "kind": st.kind,
                "unit_scale": st.unit_scale,
                "currency": st.currency,
                "periods": [p.end.isoformat() for p in st.periods],
                "matched_rows": matched,
                "rows": len(st.rows),
            }
        )
    for (ptype, pend), parts in aux.items():
        if ("sga_expense", ptype, pend) in facts or not {"_selling_marketing", "_general_admin"} <= set(
            parts
        ):
            continue
        tmpl = next((f for f in facts.values() if f.period_type == ptype and f.period_end == pend), None)
        if tmpl is None:
            continue
        facts[("sga_expense", ptype, pend)] = FactRow(
            entity_id=entity_id,
            ticker=ticker,
            canonical_field="sga_expense",
            period_end=pend,
            period_type=ptype,
            period_start=tmpl.period_start,
            fiscal_year=tmpl.fiscal_year,
            fiscal_period=tmpl.fiscal_period,
            value=parts["_selling_marketing"] + parts["_general_admin"],
            currency=tmpl.currency,
            unit_scale=tmpl.unit_scale,
            source=source,
            source_concept="label:selling and marketing + general and administrative",
            accession_or_url=accession,
            filed_at=filed_at,
            confidence=confidence,
            approved=False,
            doc_id=doc_id,
        )
    return list(facts.values()), {"tables": meta_tables, "unmatched_labels": unmatched[:60]}


def html_text(html: str, limit: int = 120_000) -> str:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style"]):
        t.decompose()
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)[:limit]


# --- network -------------------------------------------------------------------------------------------------


def archive_urls(cik: int, accession: str) -> tuple[str, str]:
    accn = accession.replace("-", "")
    return ARCHIVE_INDEX.format(cik=cik, accn=accn), ARCHIVE_BASE.format(cik=cik, accn=accn)


async def _get_text(url: str) -> str:
    from app.data.http import get_client
    from app.data.retry import raise_for_retry, retrying

    client = get_client()
    async for attempt in retrying():
        with attempt:
            resp = await client.get(url)
            raise_for_retry(resp)
    return resp.text


async def fetch_exhibits(cik: int, accession: str) -> list[tuple[str, str, str]]:
    """[(file name, url, html)] for the HTML exhibits of a filing (EX-99 files first)."""
    from app.data.registry import get_registry

    reg = get_registry()
    edgar = reg.get("edgar")
    index_url, base = archive_urls(cik, accession)
    raw = await reg.call(edgar, "archive_index", lambda: _get_text(index_url), key=accession)
    import json

    items = json.loads(raw).get("directory", {}).get("item", [])
    names = [it.get("name", "") for it in items]
    htmls = [n for n in names if n.lower().endswith((".htm", ".html")) and "-index" not in n.lower()]
    ex = [n for n in htmls if re.search(r"ex(hibit)?[-_ ]?99", n, re.I)]
    picked = (ex or htmls)[:6]
    out = []
    for n in picked:
        url = base + n
        html = await reg.call(edgar, "archive_doc", lambda url=url: _get_text(url), key=url)
        out.append((n, url, html))
    return out
