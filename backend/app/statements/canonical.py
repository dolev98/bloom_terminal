"""Resolve competing facts into one value per (field, period) and build the canonical polars frame.

Resolution order: approved filter -> restated preference (per source) -> highest confidence -> source rank ->
latest filed. Derived fallbacks (gross_profit, total_liabilities, ebitda, fcf) fill gaps and are reported back
so the service can persist them with provenance.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl

from app.statements.fields import ALL_FIELDS, DERIVED, STATEMENT_OF, derive_value

CORE_FIELDS = ("revenue", "net_income", "total_assets", "cfo")
SOURCE_RANK = {
    "edgar_xbrl": 0,
    "xbrl_org": 1,
    "6k_parsed": 2,
    "manual": 3,
    "6k_llm": 4,
    "pdf_llm": 5,
    "derived": 9,
}
_PARENT_ONLY_EQUITY = ("us-gaap:StockholdersEquity", "ifrs-full:EquityAttributableToOwnersOfParent")


def resolve(
    facts: Iterable[Any], *, period_type: str, restated: bool = True, approved_only: bool = True
) -> dict[tuple[str, date], Any]:
    """{(canonical_field, period_end): winning fact}. Works on ORM rows and FactRow alike (duck-typed)."""
    groups: dict[tuple[str, date], list[Any]] = {}
    for f in facts:
        if f.period_type != period_type:
            continue
        if approved_only and not f.approved:
            continue
        if not restated and f.restated_flag:
            continue
        groups.setdefault((f.canonical_field, f.period_end), []).append(f)
    out: dict[tuple[str, date], Any] = {}
    for key, cands in groups.items():
        if restated:
            per_source: dict[str, Any] = {}
            for c in cands:
                cur = per_source.get(c.source)
                if cur is None or (c.restated_flag and not cur.restated_flag):
                    per_source[c.source] = c
            cands = list(per_source.values())
        out[key] = max(
            cands, key=lambda c: (c.confidence, -SOURCE_RANK.get(c.source, 8), c.filed_at or date.min)
        )
    return out


def prune_empty_periods(resolved: dict[tuple[str, date], Any]) -> dict[tuple[str, date], Any]:
    """Drop periods that carry none of the core fields (stray cover-page instants, lone share counts...)."""
    with_core = {pend for (fld, pend) in resolved if fld in CORE_FIELDS}
    return {k: v for k, v in resolved.items() if k[1] in with_core}


@dataclass
class Derivation:
    field: str
    period_end: date
    value: float
    formula: str
    inputs: list[Any]


def _period_meta(resolved: dict[tuple[str, date], Any]) -> dict[date, dict]:
    meta: dict[date, dict] = {}
    by_end: dict[date, list[Any]] = {}
    for (_, pend), f in resolved.items():
        by_end.setdefault(pend, []).append(f)
    for pend, fs in by_end.items():
        fy = Counter(f.fiscal_year for f in fs if f.fiscal_year is not None).most_common(1)
        fp = Counter(f.fiscal_period for f in fs if f.fiscal_period).most_common(1)
        cur = Counter(
            f.currency
            for f in fs
            if f.currency and STATEMENT_OF.get(f.canonical_field) and f.currency != "shares"
        ).most_common(1)
        starts = [f.period_start for f in fs if f.period_start]
        meta[pend] = {
            "period_end": pend,
            "fiscal_year": fy[0][0] if fy else None,
            "fiscal_period": fp[0][0] if fp else None,
            "period_start": min(starts) if starts else None,
            "currency": cur[0][0] if cur else "",
        }
    return meta


def build_frame(resolved: dict[tuple[str, date], Any], period_type: str) -> pl.DataFrame:
    """One row per period_end (ascending), one Float64 column per canonical field (null when missing)."""
    meta = _period_meta(resolved)
    ends = sorted(meta)
    data: dict[str, list] = {
        "period_end": ends,
        "period_type": [period_type] * len(ends),
        "fiscal_year": [meta[e]["fiscal_year"] for e in ends],
        "fiscal_period": [meta[e]["fiscal_period"] for e in ends],
        "period_start": [meta[e]["period_start"] for e in ends],
        "currency": [meta[e]["currency"] for e in ends],
    }
    for fld in ALL_FIELDS:
        data[fld] = [resolved[(fld, e)].value if (fld, e) in resolved else None for e in ends]
    schema = {
        "period_end": pl.Date,
        "period_type": pl.Utf8,
        "fiscal_year": pl.Int64,
        "fiscal_period": pl.Utf8,
        "period_start": pl.Date,
        "currency": pl.Utf8,
        **{f: pl.Float64 for f in ALL_FIELDS},
    }
    return pl.DataFrame(data, schema=schema)


def apply_derived(
    frame: pl.DataFrame, resolved: dict[tuple[str, date], Any]
) -> tuple[pl.DataFrame, list[Derivation]]:
    """Fill derived fallbacks where the reported field is null. Returns the filled frame + what was derived."""
    if frame.is_empty():
        return frame, []
    rows = frame.to_dicts()
    derivations: list[Derivation] = []
    for r in rows:
        pend = r["period_end"]
        eq = resolved.get(("total_equity", pend))
        r["_equity_excludes_nci"] = bool(eq is not None and (eq.source_concept or "") in _PARENT_ONLY_EQUITY)
        for fld, (formula, inputs) in DERIVED.items():
            if r.get(fld) is not None:
                continue
            v = derive_value(fld, r)
            if v is None:
                continue
            r[fld] = v
            derivations.append(
                Derivation(
                    fld, pend, v, formula, [resolved[(i, pend)] for i in inputs if (i, pend) in resolved]
                )
            )
        r.pop("_equity_excludes_nci", None)
    filled = pl.DataFrame(rows, schema=frame.schema)
    return filled, derivations


def provenance_of(f: Any) -> dict:
    return {
        "source": f.source,
        "source_concept": f.source_concept,
        "accession_or_url": f.accession_or_url,
        "filed_at": f.filed_at.isoformat() if f.filed_at else None,
        "confidence": f.confidence,
        "restated": bool(f.restated_flag),
        "approved": bool(f.approved),
        "page": f.page,
        "unit_scale": f.unit_scale,
    }


def frame_to_payload(
    frame: pl.DataFrame, resolved: dict[tuple[str, date], Any], derivations: list[Derivation]
) -> dict:
    """JSON shape used by the API: periods, fields (values by period), provenance (by field/period)."""
    from app.statements.periods import period_label

    periods = []
    for r in frame.select(
        "period_end", "period_type", "fiscal_year", "fiscal_period", "period_start", "currency"
    ).to_dicts():
        periods.append(
            {
                **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in r.items()},
                "label": period_label(
                    r["period_type"], r["fiscal_year"], r["fiscal_period"], r["period_end"]
                ),
            }
        )
    ends = frame["period_end"].to_list()
    derived_idx = {(d.field, d.period_end): d for d in derivations}
    fields: dict[str, list[float | None]] = {}
    prov: dict[str, list[dict | None]] = {}
    for fld in ALL_FIELDS:
        fields[fld] = frame[fld].to_list()
        row: list[dict | None] = []
        for e in ends:
            f = resolved.get((fld, e))
            if f is not None:
                row.append(provenance_of(f))
            elif (fld, e) in derived_idx:
                d = derived_idx[(fld, e)]
                row.append(
                    {
                        "source": "derived",
                        "source_concept": d.formula,
                        "confidence": min([i.confidence for i in d.inputs] or [1.0]),
                        "restated": False,
                        "approved": all(i.approved for i in d.inputs),
                        "accession_or_url": None,
                        "filed_at": None,
                        "page": None,
                        "unit_scale": 0,
                    }
                )
            else:
                row.append(None)
        prov[fld] = row
    currencies = [p["currency"] for p in periods if p["currency"]]
    return {
        "periods": periods,
        "fields": fields,
        "provenance": prov,
        "currency": Counter(currencies).most_common(1)[0][0] if currencies else "",
        "statement_of": STATEMENT_OF,
    }
