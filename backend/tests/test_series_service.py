from datetime import datetime

import polars as pl
import respx
from httpx import Response

from app.data.catalog.loader import get_meta, load_seed, upsert_spec
from app.data.providers.base import SeriesSpec
from app.data.series_service import get_store, read_series, refresh_derived, refresh_series


@respx.mock
async def test_refresh_series_writes_parquet_and_meta(db):
    await load_seed()
    respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=Response(
            200,
            json={
                "observations": [
                    {"date": "2024-01-02", "value": "4.0"},
                    {"date": "2024-01-03", "value": "99.0"},
                ]
            },
        )
    )
    res = await refresh_series("fred:DGS10")
    assert res["status"] == "ok" and res["rows"] == 1 and res["dropped"] == 1  # 99% is implausible
    m = await get_meta("fred:DGS10")
    assert m["n_obs"] == 1 and m["last_status"] == "ok"
    assert read_series("fred:DGS10").height == 1


async def test_refresh_derived_from_store(db):
    await load_seed()
    st = get_store()
    st.write("fred:DGS10", pl.DataFrame({"ts": [datetime(2024, 1, 2)], "value": [4.5]}))
    st.write("fred:DGS2", pl.DataFrame({"ts": [datetime(2024, 1, 2)], "value": [4.0]}))
    spec = SeriesSpec(
        series_id="derived:US2S10S_BP",
        provider="derived",
        provider_key="US2S10S_BP",
        formula="(fred:DGS10 - fred:DGS2)*100",
    )
    await upsert_spec(spec)
    res = await refresh_derived(spec)
    assert res["status"] == "ok"
    assert read_series("derived:US2S10S_BP")["value"][0] == 50.0
