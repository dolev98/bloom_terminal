import respx
from httpx import Response

from app.data.providers.base import SeriesSpec
from app.data.providers.fred import FredProvider


def test_parse_url():
    p = FredProvider(api_key="k")
    assert p.parse_url("https://fred.stlouisfed.org/series/DGS10") == "DGS10"
    assert p.parse_url("https://fred.stlouisfed.org/graph/?id=T10Y2Y") == "T10Y2Y"
    assert p.parse_url("https://example.com") is None


@respx.mock
async def test_get_series_parses_and_drops_missing():
    respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=Response(
            200,
            json={
                "observations": [
                    {"date": "2024-01-02", "value": "4.01"},
                    {"date": "2024-01-03", "value": "."},
                    {"date": "2024-01-04", "value": "3.99"},
                ]
            },
        )
    )
    p = FredProvider(api_key="k")
    spec = SeriesSpec(series_id="fred:DGS10", provider="fred", provider_key="DGS10")
    df = await p.get_series(spec)
    assert df.height == 2
    assert df["value"].to_list() == [4.01, 3.99]


@respx.mock
async def test_describe_maps_metadata():
    respx.get("https://api.stlouisfed.org/fred/series").mock(
        return_value=Response(
            200,
            json={
                "seriess": [
                    {
                        "id": "DGS10",
                        "title": "Market Yield on U.S. Treasury Securities at 10-Year",
                        "frequency": "Daily",
                        "units_short": "%",
                        "seasonal_adjustment_short": "NSA",
                        "notes": "x",
                    }
                ]
            },
        )
    )
    p = FredProvider(api_key="k")
    spec = await p.describe("DGS10")
    assert spec.series_id == "fred:DGS10" and spec.freq == "1d" and spec.supports_vintage


def test_guessers_do_not_confuse_unemployment_rate_with_interest_rates():
    from app.data.providers.fred import _guess_category, _guess_kind, _norm_unit

    assert _guess_category("Unemployment Rate", "%") == "labour"
    assert _guess_kind("%", "Unemployment Rate") == "survey"
    assert _guess_kind("%", "Market Yield on U.S. Treasury Securities at 10-Year") == "yield"
    assert (
        _guess_category("Consumer Price Index for All Urban Consumers", "Index 1982-1984=100") == "inflation"
    )
    assert _guess_category("10-Year Breakeven Inflation Rate", "%") == "rates"
    assert (
        _norm_unit("%") == "pct"
        and _norm_unit("Bil. of $") == "usd_bn"
        and _norm_unit("Mil. of $") == "usd_mn"
    )
    assert _norm_unit("Thous. of Persons") == "thousands" and _norm_unit("Index 2017=100") == "index"
