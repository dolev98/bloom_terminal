"""US Treasury daily par yield curve (XML feed) -> risk-free rate. Fallback: catalog series `fred:DGS10`.

Feed: https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value=YYYY
Values are in percent; we return decimals.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, timedelta
from xml.etree import ElementTree as ET

from app.data.http import get_client
from app.data.retry import raise_for_retry, retrying
from app.valuation.providers.cache import get_cached, put_cached

log = logging.getLogger(__name__)
FEED_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value={year}"
TENORS = {
    "1m": "BC_1MONTH",
    "2m": "BC_2MONTH",
    "3m": "BC_3MONTH",
    "6m": "BC_6MONTH",
    "1y": "BC_1YEAR",
    "2y": "BC_2YEAR",
    "3y": "BC_3YEAR",
    "5y": "BC_5YEAR",
    "7y": "BC_7YEAR",
    "10y": "BC_10YEAR",
    "20y": "BC_20YEAR",
    "30y": "BC_30YEAR",
}
_LOCAL = re.compile(r"\{.*\}")


def parse_curve_xml(text: str) -> list[dict]:
    """Rows {date: date, '1m': float, ..., '30y': float} sorted by date (namespace-agnostic)."""
    root = ET.fromstring(text)
    rows: list[dict] = []
    for el in root.iter():
        if _LOCAL.sub("", el.tag) != "properties":
            continue
        rec: dict = {}
        for child in el:
            tag = _LOCAL.sub("", child.tag)
            val = (child.text or "").strip()
            if tag == "NEW_DATE" and val:
                rec["date"] = datetime.fromisoformat(val[:19]).date()
            else:
                for tenor, key in TENORS.items():
                    if tag == key and val:
                        try:
                            rec[tenor] = float(val)
                        except ValueError:
                            pass
        if "date" in rec:
            rows.append(rec)
    rows.sort(key=lambda r: r["date"])
    return rows


async def fetch_curve(year: int | None = None) -> list[dict]:
    year = year or datetime.now(tz=UTC).year
    client = get_client()
    async for attempt in retrying():
        with attempt:
            resp = await client.get(FEED_URL.format(year=year))
            raise_for_retry(resp)
    return parse_curve_xml(resp.text)


async def get_rf(tenor: str = "10y", max_age: timedelta = timedelta(hours=18)) -> dict | None:
    """Latest par yield for `tenor` as a decimal, with provenance. Cache -> feed -> fred:DGS10 -> None."""
    key = f"rf_us_{tenor}"
    cached = await get_cached(key, max_age)
    if cached:
        return cached
    try:
        rows = await fetch_curve()
        if not rows and datetime.now(tz=UTC).month == 1:
            rows = await fetch_curve(datetime.now(tz=UTC).year - 1)
        rows = [r for r in rows if tenor in r]
        if rows:
            last = rows[-1]
            return await put_cached(
                key,
                last[tenor] / 100.0,
                last["date"],
                "treasury",
                FEED_URL.format(year=last["date"].year),
                f"par yield {tenor}",
            )
    except Exception as e:
        log.warning("treasury feed failed: %s", e)
    fb = fallback_rf()
    if fb:
        return await put_cached(
            key, fb["value"], fb["as_of"], "fred:DGS10", "", "fallback: catalog series fred:DGS10"
        )
    stale = await get_cached(key)  # anything, even old
    return stale


def fallback_rf() -> dict | None:
    from app.data.series_service import read_series

    try:
        df = read_series("fred:DGS10")
    except Exception:
        return None
    if df.is_empty():
        return None
    last = df.drop_nulls().tail(1)
    if last.is_empty():
        return None
    ts = last["ts"][0]
    return {
        "value": float(last["value"][0]) / 100.0,
        "as_of": ts.date() if isinstance(ts, datetime) else ts,
        "source": "fred:DGS10",
    }


async def refresh_rf_series() -> dict:
    """Write the 10y par yield history for the current year into the catalog (`val:macro:rf_us10y`, decimal)."""
    import polars as pl

    from app.data.catalog.loader import upsert_spec
    from app.data.providers.base import SeriesSpec
    from app.data.series_service import write_manual

    rows = [r for r in await fetch_curve() if "10y" in r]
    if not rows:
        return {"status": "empty"}
    df = pl.DataFrame(
        {
            "ts": [datetime(r["date"].year, r["date"].month, r["date"].day) for r in rows],
            "value": [r["10y"] / 100.0 for r in rows],
        }
    ).with_columns(pl.col("ts").cast(pl.Datetime("us")))
    await upsert_spec(
        SeriesSpec(
            series_id="val:macro:rf_us10y",
            provider="val",
            provider_key="macro",
            field_name="rf_us10y",
            name="US 10y par yield (Treasury XML)",
            freq="1d",
            unit="decimal",
            value_kind="yield",
            default_transform="diff_bp",
            country="US",
            category="valuation",
            tags=["macro", "valuation"],
        )
    )
    write_manual("val:macro:rf_us10y", df)
    return {"status": "ok", "rows": df.height, "last": rows[-1]["date"].isoformat()}


def is_business_day(d: date) -> bool:
    return d.weekday() < 5
