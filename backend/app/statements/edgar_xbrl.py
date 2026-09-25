"""SEC companyfacts JSON -> canonical FactRows (pure).

Rules: point-in-time = earliest `filed` per period (restated_flag=False); latest restated row emitted only when
the most recent filing changed the value. Quarterly flows: discrete 3-month facts, or derived from YTD
differences (Q4 = FY - 9M). Balance-sheet instants keyed by end. TTM = 4 consecutive discrete quarters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.statements.concept_map import ConceptMapper
from app.statements.fields import FIELD_KIND, FactRow
from app.statements.periods import (
    approx_start,
    fiscal_year_of,
    infer_fye_month,
    is_fye,
    months_between,
    quarter_of,
)

SOURCE = "edgar_xbrl"
TAXONOMIES = ("us-gaap", "ifrs-full", "dei")
MONETARY_SKIP_UNITS = {"pure"}


@dataclass
class Entry:
    val: float
    concept: str
    accn: str
    filed: date | None
    start: date | None
    end: date


@dataclass
class _Raw:
    field: str
    priority: int
    sign: int
    concept: str
    unit: str
    start: date | None
    end: date
    val: float
    accn: str
    filed: date | None


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def collect_raw(cf: dict, mapper: ConceptMapper, entity_id: str | None = None) -> list[_Raw]:
    out: list[_Raw] = []
    seen: set[tuple] = set()
    facts = cf.get("facts", {})
    for taxonomy in TAXONOMIES:
        for concept, body in facts.get(taxonomy, {}).items():
            for unit, items in body.get("units", {}).items():
                if unit in MONETARY_SKIP_UNITS:
                    continue
                for it in items:
                    end = _d(it.get("end"))
                    if end is None or it.get("val") is None:
                        continue
                    start = _d(it.get("start"))
                    mapped = mapper.lookup(taxonomy, concept, end, entity_id)
                    if not mapped:
                        continue
                    filed = _d(it.get("filed"))
                    key = (concept, unit, start, end, filed)
                    if key in seen:
                        continue
                    seen.add(key)
                    for fld, prio, sign in mapped:
                        out.append(
                            _Raw(
                                fld,
                                prio,
                                sign,
                                f"{taxonomy}:{concept}",
                                unit,
                                start,
                                end,
                                float(it["val"]),
                                it.get("accn", ""),
                                filed,
                            )
                        )
    return out


def _period_end_like(d: date) -> bool:
    """Reporting periods end at (or within a few days of) a month end; cover-page dates like Jul 17 do not."""
    return d.day >= 24 or d.day <= 4


def snap_instants(raws: list[_Raw]) -> list[_Raw]:
    """Instants dated off a period end (dei cover-page facts) are re-keyed to the latest real period end on or
    before them, so they enrich that period instead of creating a phantom one; unsnappable ones are dropped.

    Real period ends are the ends of duration facts, plus non-cover instants that look like a month end. Cover-page
    (dei) instants are always snapped unless they coincide with a duration end: their dates (e.g. Oct 26 for a
    September FYE 10-K) can fall late in a month, where the month-end heuristic alone would keep them as a phantom
    period that also collides with the real quarter in the same fiscal bucket."""
    duration_ends = {r.end for r in raws if r.start is not None}
    real_ends = duration_ends | {
        r.end for r in raws if r.start is None and not _is_cover(r) and _period_end_like(r.end)
    }
    out: list[_Raw] = []
    for r in raws:
        if r.start is None and r.end not in duration_ends and (_is_cover(r) or not _period_end_like(r.end)):
            snap = max((e for e in real_ends if e <= r.end), default=None)
            if snap is None:
                continue
            r.end = snap
        out.append(r)
    return out


def _is_cover(r: _Raw) -> bool:
    return r.concept.startswith("dei:")


def _currency(unit: str) -> str:
    if unit == "shares":
        return ""
    return unit.split("/")[0]


def _choose_views(raws: list[_Raw]) -> dict[str, tuple[str, dict[tuple, tuple[Entry, Entry]]]]:
    """field -> (unit, {(start,end): (point_in_time_entry, latest_entry)}) using the field's dominant unit."""
    by_field_unit: dict[tuple[str, str], dict[tuple, list[_Raw]]] = {}
    for r in raws:
        by_field_unit.setdefault((r.field, r.unit), {}).setdefault((r.start, r.end), []).append(r)
    dominant: dict[str, tuple[str, int]] = {}
    for (fld, unit), periods in by_field_unit.items():
        n = len(periods)
        if fld not in dominant or n > dominant[fld][1]:
            dominant[fld] = (unit, n)
    out: dict[str, tuple[str, dict[tuple, tuple[Entry, Entry]]]] = {}
    for fld, (unit, _) in dominant.items():
        chosen: dict[tuple, tuple[Entry, Entry]] = {}
        for key, items in by_field_unit[(fld, unit)].items():
            far = date.max
            first = min(items, key=lambda r: (r.filed or far, r.priority))
            last = max(items, key=lambda r: (r.filed or date.min, -r.priority))
            chosen[key] = (
                Entry(first.val * first.sign, first.concept, first.accn, first.filed, first.start, first.end),
                Entry(last.val * last.sign, last.concept, last.accn, last.filed, last.start, last.end),
            )
        out[fld] = (unit, chosen)
    return out


def _derived(val: float, src: Entry, note: str, start: date | None, end: date) -> Entry:
    return Entry(val, f"derived:{src.concept}:{note}", src.accn, src.filed, start, end)


def build_view(
    kind: str, entries: dict[tuple, Entry], fye_month: int, *, emit_half: bool = False
) -> dict[tuple[str, date], tuple[Entry, int, str]]:
    """{(period_type, period_end): (entry, fiscal_year, fiscal_period)} for one field and one filing view.
    `emit_half` also emits H rows (IFRS half-year reporters); US filers only use 6M values for Q derivation."""
    fy_rows: dict[int, Entry] = {}
    q_rows: dict[tuple[int, int], Entry] = {}
    h_rows: dict[tuple[int, int], Entry] = {}
    ytd: dict[tuple[int, int], Entry] = {}
    for (start, end), e in entries.items():
        if kind == "stock":
            if start is not None:
                continue
            fy = fiscal_year_of(end, fye_month)
            qn = quarter_of(end, fye_month)
            prev = q_rows.get((fy, qn))
            if prev is not None and prev.end > end:
                continue  # two instants in one fiscal quarter: the later one is the quarter end
            q_rows[(fy, qn)] = e
            if is_fye(end, fye_month):
                fy_rows[fy] = e
            if emit_half and qn in (2, 4):
                h_rows[(fy, 1 if qn == 2 else 2)] = e
            continue
        if start is None:
            continue
        months = months_between(start, end)
        if months is None:
            continue
        fy = fiscal_year_of(end, fye_month)
        qn = quarter_of(end, fye_month)
        if months == 12:
            if is_fye(end, fye_month):
                fy_rows[fy] = e
        elif months == 3:
            q_rows[(fy, qn)] = e
        elif months == 6:
            if qn == 2:
                ytd[(fy, 2)] = e
            if emit_half and qn in (2, 4):
                h_rows[(fy, 1 if qn == 2 else 2)] = e
        elif months == 9 and qn == 3:
            ytd[(fy, 3)] = e

    if kind != "stock":
        _derive_quarters(kind, fy_rows, q_rows, ytd)
        if emit_half:
            for fy, e in fy_rows.items():
                h1 = h_rows.get((fy, 1))
                if (fy, 2) not in h_rows and h1 is not None and kind != "avg":
                    h_rows[(fy, 2)] = _derived(e.val - h1.val, e, "FY-H1", h1.end + timedelta(days=1), e.end)

    out: dict[tuple[str, date], tuple[Entry, int, str]] = {}
    for fy, e in fy_rows.items():
        out[("FY", e.end)] = (e, fy, "FY")
    for (fy, qn), e in q_rows.items():
        out[("Q", e.end)] = (e, fy, f"Q{qn}")
    for (fy, hn), e in h_rows.items():
        out[("H", e.end)] = (e, fy, f"H{hn}")
    for key, (e, fy) in _ttm(kind, q_rows).items():
        out[("TTM", key)] = (e, fy, "TTM")
    return out


def _combine(kind: str, parts: list[Entry]) -> Entry:
    """Cumulative entry from discrete quarters (mean for weighted-share averages, sum otherwise)."""
    vals = [p.val for p in parts]
    v = sum(vals) / len(vals) if kind == "avg" else sum(vals)
    last = parts[-1]
    return Entry(v, last.concept, last.accn, last.filed, parts[0].start, last.end)


def _derive_quarters(
    kind: str,
    fy_rows: dict[int, Entry],
    q_rows: dict[tuple[int, int], Entry],
    ytd: dict[tuple[int, int], Entry],
) -> None:
    years = set(fy_rows) | {fy for fy, _ in q_rows} | {fy for fy, _ in ytd}
    for fy in sorted(years):
        for n in (2, 3, 4):
            if (fy, n) in q_rows:
                continue
            cur = fy_rows.get(fy) if n == 4 else ytd.get((fy, n))
            if cur is None:
                continue
            prev = _ytd_prev(kind, fy, n, q_rows, ytd)
            if prev is None:
                continue
            if kind == "avg":
                val = n * cur.val - (n - 1) * prev.val
            else:
                val = cur.val - prev.val
            start = prev.end + timedelta(days=1)
            label = "FY-9M" if n == 4 else f"{n * 3}M-{(n - 1) * 3}M"
            q_rows[(fy, n)] = _derived(val, cur, label, start, cur.end)


def _ytd_prev(kind: str, fy: int, n: int, q_rows: dict, ytd: dict) -> Entry | None:
    """Cumulative value through quarter n-1: reported YTD, else assembled from discrete quarters."""
    if n == 2:
        return q_rows.get((fy, 1))
    if (fy, n - 1) in ytd:
        return ytd[(fy, n - 1)]
    parts = [q_rows.get((fy, i)) for i in range(1, n)]
    if all(p is not None for p in parts):
        return _combine(kind, parts)  # type: ignore[arg-type]
    if n == 4 and (fy, 2) in ytd and (fy, 3) in q_rows:
        y2, q3 = ytd[(fy, 2)], q_rows[(fy, 3)]
        v = (2 * y2.val + q3.val) / 3 if kind == "avg" else y2.val + q3.val
        return Entry(v, q3.concept, q3.accn, q3.filed, y2.start, q3.end)
    return None


def _consecutive(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return (b == (a[0], a[1] + 1)) or (a[1] == 4 and b == (a[0] + 1, 1))


def _ttm(kind: str, q_rows: dict[tuple[int, int], Entry]) -> dict[date, tuple[Entry, int]]:
    keys = sorted(q_rows)
    out: dict[date, tuple[Entry, int]] = {}
    for i, k in enumerate(keys):
        e = q_rows[k]
        if kind == "stock":
            out[e.end] = (Entry(e.val, e.concept, e.accn, e.filed, None, e.end), k[0])
            continue
        if i < 3:
            continue
        window = keys[i - 3 : i + 1]
        if not all(_consecutive(window[j], window[j + 1]) for j in range(3)):
            continue
        parts = [q_rows[w] for w in window]
        vals = [p.val for p in parts]
        v = sum(vals) / 4 if kind == "avg" else sum(vals)
        out[e.end] = (
            Entry(v, f"ttm:{e.concept}", e.accn, e.filed, parts[0].start or approx_start(e.end, 12), e.end),
            k[0],
        )
    return out


def _differs(a: float, b: float) -> bool:
    return abs(a - b) > max(1e-9, 1e-6 * max(abs(a), abs(b)))


def companyfacts_to_facts(
    cf: dict, mapper: ConceptMapper, *, entity_id: str, ticker: str | None, fye_month: int | None = None
) -> list[FactRow]:
    raws = snap_instants(collect_raw(cf, mapper, entity_id))
    if fye_month is None:
        fye_month = (
            infer_fye_month(r.end for r in raws if r.start and months_between(r.start, r.end) == 12) or 12
        )
    views = _choose_views(raws)
    rows: list[FactRow] = []
    for fld, (unit, chosen) in views.items():
        kind = FIELD_KIND.get(fld, "flow")
        pit = build_view(kind, {k: v[0] for k, v in chosen.items()}, fye_month)
        latest = build_view(kind, {k: v[1] for k, v in chosen.items()}, fye_month)
        currency = _currency(unit)
        for (ptype, pend), (e, fy, fp) in pit.items():
            rows.append(_row(entity_id, ticker, fld, ptype, pend, e, fy, fp, currency, restated=False))
            le = latest.get((ptype, pend))
            if le is not None and _differs(le[0].val, e.val):
                rows.append(
                    _row(entity_id, ticker, fld, ptype, pend, le[0], le[1], le[2], currency, restated=True)
                )
    return rows


def _row(
    entity_id: str,
    ticker: str | None,
    fld: str,
    ptype: str,
    pend: date,
    e: Entry,
    fy: int,
    fp: str,
    currency: str,
    *,
    restated: bool,
) -> FactRow:
    return FactRow(
        entity_id=entity_id,
        ticker=ticker,
        canonical_field=fld,
        period_end=pend,
        period_type=ptype,
        period_start=e.start,
        fiscal_year=fy,
        fiscal_period=fp,
        value=e.val,
        currency=currency,
        unit_scale=0,
        source=SOURCE,
        source_concept=e.concept,
        accession_or_url=e.accn or None,
        filed_at=e.filed,
        restated_flag=restated,
        confidence=1.0,
        approved=True,
    )


def fye_month_from_submissions(sub: dict) -> int | None:
    fye = str(sub.get("fiscalYearEnd") or "").strip()
    if len(fye) == 4 and fye[:2].isdigit():
        m = int(fye[:2])
        return m if 1 <= m <= 12 else None
    return None
