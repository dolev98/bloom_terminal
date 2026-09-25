import respx
from httpx import Response

from app.data.providers.base import SeriesSpec
from app.data.providers.edgar import EdgarProvider


@respx.mock
async def test_concept_series_quarterly_point_in_time():
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(
        return_value=Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}})
    )
    facts = {
        "units": {
            "USD": [
                {"start": "2024-01-01", "end": "2024-03-31", "val": 100, "filed": "2024-05-01"},
                {
                    "start": "2024-01-01",
                    "end": "2024-03-31",
                    "val": 101,
                    "filed": "2025-05-01",
                },  # restated later -> ignored
                {
                    "start": "2023-10-01",
                    "end": "2024-03-31",
                    "val": 250,
                    "filed": "2024-05-01",
                },  # 6-month YTD -> ignored
                {"start": "2024-04-01", "end": "2024-06-30", "val": 120, "filed": "2024-08-01"},
            ]
        }
    }
    respx.get(
        url__regex=r"https://data\.sec\.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Revenues\.json"
    ).mock(return_value=Response(200, json=facts))
    p = EdgarProvider()
    spec = SeriesSpec(
        series_id="edgar:AAPL:Revenues", provider="edgar", provider_key="AAPL", field_name="Revenues"
    )
    df = await p.get_series(spec)
    assert df["value"].to_list() == [100.0, 120.0]
