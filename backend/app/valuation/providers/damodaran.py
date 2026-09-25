"""Damodaran data: monthly implied ERP (home.htm), country risk premiums (ctryprem.xlsx) and annual industry
datasets (betas / wacc / vebitda) parsed with pandas into `industry_stats`.

File names on the site are irregular (e.g. ERPSept26.xlsx), so links are discovered from the HTML pages with
regexes and canonical URLs are kept as fallbacks. All calls go through the shared httpx client + retrying().
Results are cached in SQLite with an as_of (valuation_macro_cache / industry_stats).
"""

from __future__ import annotations

import io
import logging
import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urljoin

from sqlalchemy import select

from app.data.http import get_client
from app.data.retry import raise_for_retry, retrying
from app.data.store.sqlite import session_scope
from app.valuation.orm import IndustryStat
from app.valuation.providers.cache import get_cached, put_cached

log = logging.getLogger(__name__)

BASE = "https://pages.stern.nyu.edu/~adamodar/"
HOME_URL = BASE + "New_Home_Page/home.htm"
DATA_URL = BASE + "New_Home_Page/datacurrent.html"
CANONICAL = {
    "ctryprem": BASE + "pc/datasets/ctryprem.xlsx",
    "betas": BASE + "pc/datasets/betas.xls",
    "wacc": BASE + "pc/datasets/wacc.xls",
    "vebitda": BASE + "pc/datasets/vebitda.xls",
}
ERP_RE = re.compile(r"Implied\s+ERP\s+on\s+([A-Za-z]+\.?\s+\d{1,2},?\s+\d{4})\s*=\s*([\d.]+)\s*%", re.I)
LINK_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        1,
    )
}


async def _get(url: str):
    client = get_client()
    async for attempt in retrying():
        with attempt:
            resp = await client.get(url)
            raise_for_retry(resp)
    return resp


def _parse_erp_date(s: str) -> date | None:
    s = s.replace(",", "").replace(".", "").strip()
    parts = s.split()
    if len(parts) != 3:
        return None
    mon = parts[0].lower()
    month = MONTHS.get(mon) or next((v for k, v in MONTHS.items() if k.startswith(mon[:3])), None)
    if not month:
        return None
    try:
        return date(int(parts[2]), month, int(parts[1]))
    except ValueError:
        return None


def parse_implied_erp(html: str) -> tuple[float, date | None] | None:
    """Finds 'Implied ERP on <date> = x.xx%' in home.htm -> (decimal, date)."""
    m = ERP_RE.search(html)
    if not m:
        return None
    return float(m.group(2)) / 100.0, _parse_erp_date(m.group(1))


def find_links(html: str, page_url: str, pattern: str) -> list[str]:
    """Absolute hrefs whose file name matches `pattern` (regex, case-insensitive)."""
    rx = re.compile(pattern, re.I)
    out = []
    for href in LINK_RE.findall(html):
        if rx.search(href.rsplit("/", 1)[-1]):
            out.append(urljoin(page_url, href))
    return out


# --- ERP -------------------------------------------------------------------------


async def get_erp(max_age: timedelta = timedelta(days=7)) -> dict | None:
    cached = await get_cached("erp_us", max_age)
    if cached:
        return cached
    try:
        resp = await _get(HOME_URL)
        parsed = parse_implied_erp(resp.text)
        if parsed:
            value, as_of = parsed
            return await put_cached("erp_us", value, as_of, "damodaran", HOME_URL, "implied ERP (home.htm)")
        log.warning("damodaran: implied ERP line not found on home.htm")
    except Exception as e:
        log.warning("damodaran ERP fetch failed: %s", e)
    return await get_cached("erp_us")


# --- Country risk premium ------------------------------------------------------------


def parse_ctryprem(content: bytes, country: str) -> dict | None:
    """Locate `country` in the 'ERPs by country' sheet: returns {crp, erp, rating, default_spread} as decimals."""
    import pandas as pd

    sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None)
    for name, df in sheets.items():
        if "country" not in name.lower() and "erp" not in name.lower():
            continue
        df = df.dropna(how="all")
        header_idx = None
        for i, row in df.iterrows():
            cells = [str(c).strip().lower() for c in row.tolist()]
            if any(c == "country" for c in cells) and any("country risk premium" in c for c in cells):
                header_idx = i
                break
        if header_idx is None:
            continue
        header = [str(c).strip().lower() for c in df.loc[header_idx].tolist()]
        body = df.loc[df.index > header_idx]
        ci = header.index("country")
        crp_i = next(i for i, c in enumerate(header) if "country risk premium" in c)
        erp_i = next(
            (
                i
                for i, c in enumerate(header)
                if c.startswith("equity risk premium") or c == "total equity risk premium"
            ),
            None,
        )
        rating_i = next((i for i, c in enumerate(header) if "rating" in c), None)
        ds_i = next((i for i, c in enumerate(header) if "default spread" in c), None)
        for _, row in body.iterrows():
            if str(row.iloc[ci]).strip().lower() == country.strip().lower():
                out = {"country": country, "crp": _pct(row.iloc[crp_i])}
                if erp_i is not None:
                    out["erp"] = _pct(row.iloc[erp_i])
                if rating_i is not None:
                    out["rating"] = str(row.iloc[rating_i])
                if ds_i is not None:
                    out["default_spread"] = _pct(row.iloc[ds_i])
                return out
    return None


def _pct(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x / 100.0 if abs(x) > 1.0 else x


async def _discover(kind: str) -> str:
    try:
        resp = await _get(DATA_URL)
        links = find_links(resp.text, DATA_URL, rf"^{kind}[^/]*\.xlsx?$")
        if links:
            return links[0]
    except Exception as e:
        log.debug("damodaran link discovery failed for %s: %s", kind, e)
    return CANONICAL[kind]


async def get_crp(country: str = "Israel", max_age: timedelta = timedelta(days=30)) -> dict | None:
    key = f"crp_{country.lower().replace(' ', '_')}"
    cached = await get_cached(key, max_age)
    if cached:
        return cached
    try:
        url = await _discover("ctryprem")
        resp = await _get(url)
        row = parse_ctryprem(resp.content, country)
        if row and row.get("crp") is not None:
            as_of = _as_of_from_name(url) or datetime.now(tz=UTC).date().replace(day=1)
            return await put_cached(
                key,
                row["crp"],
                as_of,
                "damodaran",
                url,
                f"rating {row.get('rating')}, default spread {row.get('default_spread')}",
            )
        log.warning("damodaran: country %s not found in ctryprem", country)
    except Exception as e:
        log.warning("damodaran CRP fetch failed: %s", e)
    return await get_cached(key)


def _as_of_from_name(url: str) -> date | None:
    m = re.search(r"(\d{2})\.xlsx?$", url) or re.search(r"(20\d{2})", url)
    if not m:
        return None
    y = int(m.group(1))
    y = y + 2000 if y < 100 else y
    return date(y, 1, 1)


# --- Industry datasets ------------------------------------------------------------


def parse_industry_table(content: bytes, filename: str = "") -> list[dict]:
    """Parse an industry-averages sheet: header row contains 'Industry Name'; returns [{industry, <col>: value}]."""
    import pandas as pd

    engine = "xlrd" if filename.lower().endswith(".xls") else None
    sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, engine=engine)
    preferred = [n for n in sheets if "industry" in n.lower()] + [
        n for n in sheets if "industry" not in n.lower()
    ]
    for name in preferred:
        df = sheets[name].dropna(how="all")
        header_idx = None
        for i, row in df.iterrows():
            cells = [str(c).strip().lower() for c in row.tolist()]
            if any(c == "industry name" or c == "industry" for c in cells):
                header_idx = i
                break
        if header_idx is None:
            continue
        header = [str(c).strip() for c in df.loc[header_idx].tolist()]
        ind_i = next(i for i, c in enumerate(header) if c.lower() in ("industry name", "industry"))
        rows = []
        for _, row in df.loc[df.index > header_idx].iterrows():
            industry = str(row.iloc[ind_i]).strip()
            if not industry or industry.lower() in (
                "nan",
                "total market",
                "total market (without financials)",
            ):
                continue
            vals: dict[str, float] = {}
            for j, col in enumerate(header):
                if j == ind_i or not col or col.lower() == "nan":
                    continue
                try:
                    x = float(row.iloc[j])
                except (TypeError, ValueError):
                    continue
                if x == x:  # not NaN
                    vals[_norm_col(col)] = x
            if vals:
                rows.append({"industry": industry, **vals})
        if rows:
            return rows
    return []


def _norm_col(c: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", c.strip().lower()).strip("_")


async def refresh_industry_stats(
    kinds: tuple[str, ...] = ("betas", "wacc", "vebitda"), region: str = "US"
) -> dict:
    """Download + parse the annual datasets; upsert into industry_stats with as_of = Jan 1 of the data year."""
    out: dict[str, int] = {}
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    for kind in kinds:
        try:
            url = await _discover(kind)
            resp = await _get(url)
            rows = parse_industry_table(resp.content, url)
            as_of = _as_of_from_name(url) or date(now.year, 1, 1)
            async with session_scope() as s:
                for r in rows:
                    ind = r["industry"]
                    vals = {k: v for k, v in r.items() if k != "industry"}
                    existing = (
                        await s.execute(
                            select(IndustryStat).where(
                                IndustryStat.dataset == kind,
                                IndustryStat.region == region,
                                IndustryStat.industry == ind,
                                IndustryStat.as_of == as_of,
                            )
                        )
                    ).scalar_one_or_none()
                    if existing is None:
                        s.add(
                            IndustryStat(
                                dataset=kind,
                                region=region,
                                industry=ind,
                                as_of=as_of,
                                values=vals,
                                source_url=url,
                                fetched_at=now,
                            )
                        )
                    else:
                        existing.values, existing.source_url, existing.fetched_at = vals, url, now
            out[kind] = len(rows)
        except Exception as e:
            log.warning("damodaran %s failed: %s", kind, e)
            out[kind] = -1
    return out


async def industry_stats(dataset: str | None = None, region: str = "US") -> list[dict]:
    async with session_scope() as s:
        stmt = select(IndustryStat).where(IndustryStat.region == region)
        if dataset:
            stmt = stmt.where(IndustryStat.dataset == dataset)
        rows = (
            (
                await s.execute(
                    stmt.order_by(IndustryStat.dataset, IndustryStat.industry, IndustryStat.as_of.desc())
                )
            )
            .scalars()
            .all()
        )
    seen: set[tuple[str, str]] = set()
    out = []
    for r in rows:  # latest as_of per (dataset, industry)
        k = (r.dataset, r.industry)
        if k in seen:
            continue
        seen.add(k)
        out.append(
            {
                "dataset": r.dataset,
                "industry": r.industry,
                "as_of": r.as_of.isoformat(),
                "values": r.values,
                "url": r.source_url,
            }
        )
    return out


async def industry_beta_for(ticker: str) -> float | None:
    """Unlevered industry beta for the ticker's Damodaran industry (peer_sets.industry, user-set). None when unknown."""
    from app.valuation.orm import PeerSet

    async with session_scope() as s:
        ps = await s.get(PeerSet, ticker.upper())
        if ps is None or not ps.industry:
            return None
        row = (
            (
                await s.execute(
                    select(IndustryStat)
                    .where(IndustryStat.dataset == "betas", IndustryStat.industry == ps.industry)
                    .order_by(IndustryStat.as_of.desc())
                )
            )
            .scalars()
            .first()
        )
    if row is None:
        return None
    for k, v in row.values.items():
        if "unlevered_beta" in k and "cash" not in k:
            return float(v)
    return None


async def macro_snapshot() -> dict:
    """rf / erp / crp with as_of + industry dataset dates (for GET /api/valuation/macro)."""
    from app.valuation.providers.cache import all_cached

    cached = await all_cached()
    async with session_scope() as s:
        rows = (await s.execute(select(IndustryStat.dataset, IndustryStat.as_of).distinct())).all()
    ind = {}
    for ds, as_of in rows:
        ind[ds] = max(ind.get(ds, as_of.isoformat()), as_of.isoformat())
    return {"macro": cached, "industry_stats_as_of": ind}
