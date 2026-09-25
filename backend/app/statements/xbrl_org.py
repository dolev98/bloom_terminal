"""filings.xbrl.org (ESEF / IFRS) adapter: JSON:API filing search + xBRL-JSON facts -> canonical FactRows.

No API key. Provider instance exposed as PROVIDERS for the registry; calls go through `registry.call` when the
registry knows the provider, otherwise through a module-level instance (rate limit still applied).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.data.http import get_client
from app.data.providers.base import Capability, LicenseSpec, Provider, RateSpec
from app.data.retry import raise_for_retry, retrying
from app.statements.concept_map import ConceptMapper
from app.statements.edgar_xbrl import Entry, build_view
from app.statements.fields import FIELD_KIND, FactRow
from app.statements.periods import infer_fye_month, months_between

BASE = "https://filings.xbrl.org"
API = f"{BASE}/api/filings"
SOURCE = "xbrl_org"
CORE_DIMS = {"concept", "entity", "period", "unit", "language"}


@dataclass
class XbrlOrgProvider(Provider):
    id: str = "xbrl_org"
    name: str = "filings.xbrl.org (ESEF/IFRS)"
    capabilities: Capability = Capability.STATEMENTS
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_second=2, concurrency=2))
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(
            grey=False, attribution="Source: filings.xbrl.org (XBRL International)"
        )
    )

    async def health(self) -> dict:
        try:
            await _json(API, {"page[size]": "1"})
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}


PROVIDERS = [XbrlOrgProvider()]
_fallback = PROVIDERS[0]


def _provider() -> Provider:
    from app.data.registry import get_registry

    try:
        return get_registry().get("xbrl_org")
    except Exception:
        return _fallback


async def _json(url: str, params: dict | None = None) -> dict:
    client = get_client()
    async for attempt in retrying():
        with attempt:
            resp = await client.get(
                url, params=params, headers={"Accept": "application/vnd.api+json, application/json"}
            )
            raise_for_retry(resp)
    return resp.json()


async def _call(op: str, fn, key: str = ""):
    from app.data.registry import get_registry

    return await get_registry().call(_provider(), op, fn, key=key)


def _abs(u: str | None) -> str | None:
    if not u:
        return None
    return u if u.startswith("http") else BASE + (u if u.startswith("/") else "/" + u)


def normalize_filing(item: dict, included: list[dict] | None = None) -> dict:
    a = item.get("attributes", {})
    ent_id = item.get("relationships", {}).get("entity", {}).get("data", {}).get("id")
    ent = next((i for i in included or [] if i.get("type") == "entity" and i.get("id") == ent_id), {})
    ea = ent.get("attributes", {})
    return {
        "id": item.get("id"),
        "fxo_id": a.get("fxo_id"),
        "lei": ea.get("identifier") or (a.get("fxo_id") or "").split("-")[0] or None,
        "entity_name": ea.get("name"),
        "country": a.get("country"),
        "period_end": a.get("period_end"),
        "date_added": a.get("date_added"),
        "json_url": _abs(a.get("json_url")),
        "report_url": _abs(a.get("report_url")),
        "package_url": _abs(a.get("package_url")),
    }


async def search_filings(*, lei: str | None = None, name: str | None = None, limit: int = 20) -> list[dict]:
    if lei:
        flt = [{"name": "entity.identifier", "op": "eq", "val": lei}]
    elif name:
        flt = [{"name": "entity.name", "op": "ilike", "val": f"%{name}%"}]
    else:
        raise ValueError("lei or name required")
    params = {"filter": json.dumps(flt), "include": "entity", "sort": "-date_added", "page[size]": str(limit)}
    data = await _call("search", lambda: _json(API, params), key=lei or name or "")
    included = data.get("included", [])
    return [normalize_filing(it, included) for it in data.get("data", [])]


async def fetch_facts_json(json_url: str) -> dict:
    return await _call("facts", lambda: _json(json_url), key=json_url)


def _period(p: str) -> tuple[date | None, date]:
    """OIM period -> (start, end). Midnight end datetimes denote the end of the previous day."""

    def _d(s: str, is_end: bool) -> date:
        dt = (
            datetime.fromisoformat(s.replace("Z", "+00:00"))
            if "T" in s
            else datetime.fromisoformat(s + "T00:00:00")
        )
        d = dt.date()
        if is_end and dt.hour == 0 and dt.minute == 0 and "T" in s:
            d = d - timedelta(days=1)
        return d

    if "/" in p:
        a, b = p.split("/", 1)
        return _d(a, False), _d(b, True)
    return None, _d(p, True)


def _unit(u: str | None) -> str:
    if not u:
        return ""
    u = u.replace("iso4217:", "").replace("xbrli:", "")
    return u


def xbrl_json_to_facts(
    doc: dict,
    mapper: ConceptMapper,
    *,
    entity_id: str,
    ticker: str | None,
    fye_month: int | None = None,
    url: str | None = None,
    filed_at: date | None = None,
) -> list[FactRow]:
    """xBRL-JSON (OIM) facts without extra dimensions -> FactRows (annual FY rows; half-years as H)."""
    entries: dict[tuple[str, str], dict[tuple, Entry]] = {}
    cur_prio: dict[tuple, int] = {}
    for f in doc.get("facts", {}).values():
        dims = f.get("dimensions", {})
        if set(dims) - CORE_DIMS:
            continue
        concept = dims.get("concept", "")
        if ":" not in concept or f.get("value") is None:
            continue
        taxonomy, local = concept.split(":", 1)
        try:
            val = float(f["value"])
        except (TypeError, ValueError):
            continue
        start, end = _period(dims.get("period", ""))
        unit = _unit(dims.get("unit"))
        if unit == "pure":
            continue
        for fld, prio, sign in mapper.lookup(taxonomy, local, end, entity_id):
            bucket = entries.setdefault((fld, unit), {})
            key = (start, end)
            e = Entry(val * sign, concept, url or "", filed_at, start, end)
            cur = bucket.get(key)
            if cur is None or prio < cur_prio.get((fld, unit, key), 99):
                bucket[key] = e
                cur_prio[(fld, unit, key)] = prio
    if fye_month is None:
        ends = [k[1] for b in entries.values() for k in b if k[0] and months_between(k[0], k[1]) == 12]
        fye_month = infer_fye_month(ends) or 12
    rows: list[FactRow] = []
    dominant: dict[str, str] = {}
    for (fld, unit), b in entries.items():
        if fld not in dominant or len(b) > len(entries[(fld, dominant[fld])]):
            dominant[fld] = unit
    for fld, unit in dominant.items():
        kind = FIELD_KIND.get(fld, "flow")
        view = build_view(kind, entries[(fld, unit)], fye_month, emit_half=True)
        currency = "" if unit == "shares" else unit.split("/")[0]
        for (ptype, pend), (e, fy, fp) in view.items():
            rows.append(
                FactRow(
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
                    accession_or_url=url,
                    filed_at=filed_at,
                    restated_flag=False,
                    confidence=1.0,
                    approved=True,
                )
            )
    return rows
