import respx
from httpx import Response

from app.data.providers.base import SeriesSpec
from app.data.providers.boi import BoiProvider

CSV = "DATAFLOW,SERIES_CODE,TIME_PERIOD,OBS_VALUE\nBOI.STATISTICS:EXR(1.0),RER_USD_ILS,2026-09-01,3.012\nBOI.STATISTICS:EXR(1.0),RER_USD_ILS,2026-09-04,3.005\n"


@respx.mock
async def test_boi_csv_parsing():
    respx.get(url__regex=r"https://edge\.boi\.org\.il/.*EXR/1\.0/RER_USD_ILS.*").mock(
        return_value=Response(200, text=CSV)
    )
    p = BoiProvider()
    spec = SeriesSpec(series_id="boi:EXR/RER_USD_ILS", provider="boi", provider_key="EXR/RER_USD_ILS")
    df = await p.get_series(spec)
    assert df.height == 2 and df["value"][1] == 3.005


def test_parse_url_and_describe_kind():
    p = BoiProvider()
    assert (
        p.parse_url(
            "https://edge.boi.org.il/FusionEdgeServer/sdmx/v2/data/dataflow/BOI.STATISTICS/BR/1.0/MNT_RIB_BOI_D?format=csv"
        )
        == "BR/MNT_RIB_BOI_D"
    )


async def test_describe_gives_readable_fx_name():
    from app.data.providers.boi import BoiProvider

    spec = await BoiProvider().describe("EXR/RER_EUR_ILS")
    assert spec.name == "EUR/ILS representative exchange rate"
