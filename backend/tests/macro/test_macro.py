from datetime import date, datetime, timedelta

import polars as pl
import respx
from httpx import Response

from app.api.routers import macro as macro_router
from app.data.catalog.loader import get_spec, load_seed
from app.data.providers.base import SeriesSpec
from app.data.series_service import get_store
from app.macro import service
from app.macro.providers import PROVIDERS
from app.macro.providers.cbs import CbsProvider, parse_cbs_price
from app.macro.providers.ons import OnsProvider, parse_ons
from app.macro.providers.sdmx_generic import (
    BisProvider,
    EstatProvider,
    ImfProvider,
    OecdProvider,
    parse_period,
    parse_sdmx_csv,
)
from app.main import app

if not any(getattr(r, "path", "").startswith("/api/macro") for r in app.routes):
    from starlette.routing import Mount

    app.include_router(macro_router.router)
    for m in [r for r in app.router.routes if isinstance(r, Mount)]:  # keep the SPA catch-all last
        app.router.routes.remove(m)
        app.router.routes.append(m)


def monthly(start: date, values: list[float]) -> pl.DataFrame:
    ts = []
    y, m = start.year, start.month
    for _ in values:
        ts.append(datetime(y, m, 1))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return pl.DataFrame({"ts": ts, "value": values}).with_columns(pl.col("ts").cast(pl.Datetime("us")))


def quarterly(start: date, values: list[float]) -> pl.DataFrame:
    ts = []
    y, m = start.year, start.month
    for _ in values:
        ts.append(datetime(y, m, 1))
        m += 3
        if m > 12:
            y, m = y + 1, m - 12
    return pl.DataFrame({"ts": ts, "value": values}).with_columns(pl.col("ts").cast(pl.Datetime("us")))


async def test_country_dashboard_on_synthetic(db):
    await load_seed()
    added = await service.ensure_catalog(force=True)
    assert (
        added > 20 and await get_spec("fred:PPIFIS") is not None and await get_spec("cbs:120010") is not None
    )
    assert await service.ensure_catalog() == 0
    st = get_store()
    now = datetime.now()
    start_m = date(now.year - 4, now.month, 1)
    n = 48
    cpi = [100 * (1.003**i) for i in range(n)]
    st.write("fred:CPIAUCSL", monthly(start_m, cpi))
    st.write("fred:UNRATE", monthly(start_m, [4.0 + 0.02 * i for i in range(n)]))
    st.write("fred:PAYEMS", monthly(start_m, [150000 + 150 * i for i in range(n)]))
    st.write("fred:USREC", monthly(start_m, [1 if 5 <= i <= 8 else 0 for i in range(n)]))
    gdp = [20000 * (1.006**i) for i in range(16)]
    st.write("fred:GDPC1", quarterly(date(now.year - 4, ((now.month - 1) // 3) * 3 + 1, 1), gdp))
    st.write("fred:GDPNOW", quarterly(date(now.year - 1, 1, 1), [2.1, 2.4, 3.0, 2.8]))
    dash = await service.country_dashboard("US")
    assert dash["country"] == "US" and [s["name"] for s in dash["sections"]][:2] == ["growth", "inflation"]
    tiles = {t["indicator"]: t for s in dash["sections"] for t in s["tiles"]}
    cpi_t = tiles["cpi"]
    assert (
        cpi_t["transform"] == "yoy"
        and abs(cpi_t["last"] - (1.003**12 - 1) * 100) < 1e-6
        and len(cpi_t["sparkline"]["value"]) == 24
    )
    assert cpi_t["zscore_3y"] is not None and cpi_t["stale"] is False and cpi_t["unit"] == "%"
    assert tiles["unemployment"]["last"] > 4.9 and tiles["unemployment"]["yoy"] is None
    assert tiles["payrolls"]["transform"] == "diff" and abs(tiles["payrolls"]["last"] - 150) < 1e-6
    assert (
        tiles["gdp_yoy"]["last"] is not None
        and tiles["ppi"]["last"] is None
        and tiles["ppi"]["stale"] is True
    )
    assert dash["recessions"] and dash["recessions"][0]["start"].endswith("-01")
    assert dash["nowcasts"][0]["value"] == 2.8
    assert (
        dash["regime"]["growth_z"] is not None
        and dash["regime"]["inflation_z"] is not None
        and len(dash["regime"]["trail"]) == 12
    )
    assert dash["regime"]["label"] in ("reflation", "goldilocks", "stagflation", "deflationary slowdown")
    cmp_ = await service.compare("cpi", ["US", "IL"])
    assert (
        cmp_["series"]["US"]["n"] == n
        and cmp_["series"]["IL"]["n"] == 0
        and len(cmp_["ts"]) > 0
        and "US" in cmp_["table"]
    )
    hm = await service.heatmap(["US", "IL"], ["cpi", "unemployment", "gdp_yoy"])
    cell = next(c for c in hm["cells"] if c["country"] == "US" and c["indicator"] == "cpi")
    assert cell["z"] == cpi_t["zscore_3y"] and hm["names"]["cpi"] == "CPI (YoY)"


def test_technical_recessions():
    df = quarterly(date(2020, 1, 1), [100, 101, 100, 99, 98, 99, 100, 101])
    rec = service.technical_recessions(df)
    assert rec == [{"start": "2020-07-01", "end": "2021-04-01"}]
    assert service.technical_recessions(quarterly(date(2020, 1, 1), [100, 101, 102])) == []


def test_provider_ids_and_parsers():
    assert [p.id for p in PROVIDERS] == ["imf", "bis", "ecb", "estat", "oecd", "cbs", "ons"]
    assert (
        parse_period("2025-M01") == date(2025, 1, 1)
        and parse_period("2026-Q2") == date(2026, 4, 1)
        and parse_period("2025-02-05") == date(2025, 2, 5)
        and parse_period("2024") == date(2024, 1, 1)
    )
    csv = "DATAFLOW,COUNTRY,TIME_PERIOD,OBS_VALUE,NOTES\nIMF.STA:CPI(5.0.0),USA,2025-M02,146.1,x\nIMF.STA:CPI(5.0.0),USA,2025-M01,145.6,x\nIMF.STA:CPI(5.0.0),USA,2025-M03,,x\n"
    df = parse_sdmx_csv(csv)
    assert df["value"].to_list() == [145.6, 146.1] and df["ts"].dtype == pl.Datetime("us")
    assert (
        ImfProvider().parse_url(
            "https://api.imf.org/external/sdmx/2.1/data/CPI/USA.CPI._T.IX.M?startPeriod=2025"
        )
        == "CPI/USA.CPI._T.IX.M"
    )
    assert (
        BisProvider().parse_url("https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/M.IL?format=csv")
        == "WS_CBPOL/M.IL"
    )
    assert (
        EstatProvider().parse_url(
            "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/prc_hicp_manr/M.RCH_A.CP00.EA20?format=SDMX-CSV"
        )
        == "prc_hicp_manr/M.RCH_A.CP00.EA20"
    )
    assert OecdProvider().rate.per_minute == 1
    cbs = {
        "month": [
            {
                "code": 120010,
                "date": [
                    {
                        "year": 2026,
                        "month": 8,
                        "percent": 0.7,
                        "percentYear": 1.5,
                        "currBase": {"value": 105.8},
                    },
                    {
                        "year": 2026,
                        "month": 7,
                        "percent": 0.3,
                        "percentYear": 1.5,
                        "currBase": {"value": 105.1},
                    },
                ],
            }
        ]
    }
    assert parse_cbs_price(cbs)["value"].to_list() == [105.1, 105.8]
    assert parse_cbs_price(cbs, "pct")["value"].to_list() == [0.3, 0.7]
    assert (
        CbsProvider().egress == "il"
        and CbsProvider().parse_url("https://api.cbs.gov.il/index/data/price?id=120010&format=json")
        == "120010"
    )
    ons = {
        "months": [{"date": "2026 JUL", "value": "3.0"}, {"date": "2026 AUG", "value": "3.1"}],
        "quarters": [],
        "years": [],
    }
    df, freq = parse_ons(ons)
    assert freq == "1mo" and df["value"].to_list() == [3.0, 3.1] and df["ts"][-1] == datetime(2026, 8, 1)
    assert (
        OnsProvider().parse_url(
            "https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7g7/mm23/data"
        )
        == "D7G7/MM23"
    )


@respx.mock
async def test_sdmx_and_cbs_and_ons_fetch():
    respx.get(url__regex=r"https://stats\.bis\.org/api/v2/data/dataflow/BIS/WS_CBPOL/1\.0/M\.IL.*").mock(
        return_value=Response(
            200, text="FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE\nM,IL,2025-01,4.5\nM,IL,2025-02,4.5\n"
        )
    )
    df = await BisProvider().get_series(
        SeriesSpec(series_id="bis:WS_CBPOL/M.IL", provider="bis", provider_key="WS_CBPOL/M.IL"),
        since=date(2025, 1, 1),
    )
    assert df.height == 2 and df["value"][-1] == 4.5
    imf_route = respx.get(
        url__regex=r"https://api\.imf\.org/external/sdmx/2\.1/data/CPI/ISR\.CPI\._T\.IX\.M.*"
    ).mock(return_value=Response(200, text="DATAFLOW,TIME_PERIOD,OBS_VALUE\nx,2026-M08,105.8\n"))
    df = await ImfProvider().get_series(
        SeriesSpec(series_id="imf:CPI/ISR.CPI._T.IX.M", provider="imf", provider_key="CPI/ISR.CPI._T.IX.M")
    )
    assert df["value"][0] == 105.8 and "csv" in imf_route.calls[0].request.headers["Accept"]
    respx.get(url__regex=r"https://sdmx\.oecd\.org/public/rest/data/.*").mock(
        return_value=Response(404, text="NoResultsFound")
    )
    df = await OecdProvider().get_series(
        SeriesSpec(series_id="oecd:A,B,1/Q.X", provider="oecd", provider_key="A,B,1/Q.X")
    )
    assert df.is_empty()
    cbs_route = respx.get("https://api.cbs.gov.il/index/data/price").mock(
        return_value=Response(
            200,
            json={
                "month": [
                    {
                        "code": 120010,
                        "date": [
                            {
                                "year": 2026,
                                "month": 8,
                                "percent": 0.7,
                                "percentYear": 1.5,
                                "currBase": {"value": 105.8},
                            }
                        ],
                    }
                ]
            },
        )
    )
    df = await CbsProvider().get_series(
        SeriesSpec(series_id="cbs:120010", provider="cbs", provider_key="120010")
    )
    assert df["value"][0] == 105.8 and "Mozilla" in cbs_route.calls[0].request.headers["User-Agent"]
    respx.get("https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7g7/mm23/data").mock(
        return_value=Response(200, json={"months": [{"date": "2026 AUG", "value": "3.1"}]})
    )
    df = await OnsProvider().get_series(
        SeriesSpec(series_id="ons:D7G7/MM23", provider="ons", provider_key="D7G7/MM23")
    )
    assert df["value"][0] == 3.1
    respx.get(url__regex=r"https://www\.ons\.gov\.uk/.*/timeseries/mgsx/lms/data").mock(
        side_effect=lambda req: (
            Response(200, json={"months": [{"date": "2026 JUL", "value": "4.7"}]})
            if "peoplenotinwork" in str(req.url)
            else Response(404)
        )
    )
    df = await OnsProvider().get_series(
        SeriesSpec(series_id="ons:MGSX/LMS", provider="ons", provider_key="MGSX/LMS")
    )
    assert df["value"][0] == 4.7


def test_macro_api(client):
    ccs = client.get("/api/macro/countries").json()
    assert {c["cc"] for c in ccs} == {"US", "IL", "EA", "DE", "GB", "JP", "CN"}
    us = client.get("/api/macro/US").json()
    assert us["country"] == "US" and len(us["sections"]) == 7 and us["regime"]["growth_z"] is None
    assert client.get("/api/macro/XX").status_code == 404
    hm = client.get("/api/macro/heatmap", params={"countries": "US,IL", "indicators": "cpi,policy"}).json()
    assert len(hm["cells"]) == 4
    cmp_ = client.get("/api/macro/compare", params={"indicator": "cpi", "countries": "US,GB"}).json()
    assert cmp_["series"]["GB"]["series_id"] == "ons:D7G7/MM23"
    assert client.get("/api/catalog").json()["count"] > 80  # macro series were added to the catalog
    assert timedelta(days=1)


def weekly(start: date, values: list[float]) -> pl.DataFrame:
    ts = [datetime(start.year, start.month, start.day) + timedelta(weeks=i) for i in range(len(values))]
    return pl.DataFrame({"ts": ts, "value": values}).with_columns(pl.col("ts").cast(pl.Datetime("us")))


async def test_display_overrides_scale_units_and_names(db):
    """Claims are stored as a count and the trade balance in $ millions; tiles must be in the unit they claim."""
    await load_seed()
    await service.ensure_catalog()
    st = get_store()
    now = datetime.now()
    wk0 = (now - timedelta(weeks=59)).date()
    st.write("fred:ICSA", weekly(wk0, [210000.0 + 1000 * (i % 5) for i in range(59)] + [196000.0]))
    m0 = date(now.year - 3, now.month, 1)
    st.write("fred:BOPGSTB", monthly(m0, [-70000.0 - 100 * i for i in range(35)] + [-88576.0]))
    us = await service.country_dashboard("US")
    tiles = {t["indicator"]: t for s in us["sections"] for t in s["tiles"]}
    claims, tb = tiles["claims"], tiles["trade_balance"]
    assert claims["unit"] == "k" and claims["last"] == 196.0 and max(claims["sparkline"]["value"]) < 1000
    assert abs(tb["last"] - (-88.576)) < 1e-9 and tb["unit"] == "USD bn"
    assert abs(tb["change"] - (-88.576 + 73.4)) < 1e-9
    hm = await service.heatmap(["US"], ["claims"])
    assert hm["cells"][0]["last"] == 196.0
    # Israel: the 10Y yield is not re-labelled as a breakeven, and the IL-US spread is named/unit-ed honestly
    il = await service.mapping_for("IL")
    assert "breakeven_10y" not in il["inflation"]
    assert service.entry_overrides("IL", "curve") == {"name": "Israel − US 10Y spread", "unit": "pp"}
    assert service.entry_overrides("US", "curve") == {}


def test_monthly_series_one_release_behind_is_not_stale():
    spec = SeriesSpec(
        series_id="fred:BOPGSTB",
        provider="fred",
        provider_key="BOPGSTB",
        name="US trade balance",
        freq="1mo",
        unit="usd_mn",
        value_kind="flow",
    )
    now = datetime(2026, 9, 24)
    df = monthly(date(2024, 1, 1), [float(i) for i in range(31)])  # last obs = Jul 2026 (published early Sep)
    t = service.tile_from_frame(
        df, spec, "trade_balance", spec.series_id, "level", "Trade balance", "bn", now=now
    )
    assert t["last_ts"] == datetime(2026, 7, 1) and t["stale"] is False
    old = monthly(date(2024, 1, 1), [float(i) for i in range(28)])  # last obs = Apr 2026 -> genuinely stale
    assert service.tile_from_frame(old, spec, "x", spec.series_id, "level", "x", "", now=now)["stale"] is True


@respx.mock
async def test_estat_and_oecd_request_sdmx_csv_by_media_type():
    """Eurostat answers 406 to Accept: text/csv; OECD answers 500 to format=csvfile or a parameter-less query."""
    estat = respx.get(
        url__regex=r"https://ec\.europa\.eu/eurostat/api/dissemination/sdmx/2\.1/data/prc_hicp_minr/.*"
    ).mock(
        side_effect=lambda req: (
            Response(
                200,
                text="DATAFLOW,LAST UPDATE,freq,unit,coicop18,geo,TIME_PERIOD,OBS_VALUE,OBS_FLAG\n"
                "ESTAT:PRC_HICP_MINR(1.0),17/09/26,M,RCH_A,TOTAL,EA21,2026-07,3.0,\n"
                "ESTAT:PRC_HICP_MINR(1.0),17/09/26,M,RCH_A,TOTAL,EA21,2026-08,3.2,\n",
            )
            if req.headers.get("Accept", "").startswith("application/vnd.sdmx.data+csv")
            else Response(406, text="Not Acceptable")
        )
    )
    spec = SeriesSpec(
        series_id="estat:prc_hicp_minr/M.RCH_A.TOTAL.EA21",
        provider="estat",
        provider_key="prc_hicp_minr/M.RCH_A.TOTAL.EA21",
    )
    df = await EstatProvider().get_series(spec)
    assert df.height == 2 and df["value"][-1] == 3.2
    req = estat.calls[0].request
    assert req.url.params["format"] == "SDMX-CSV" and "text/csv" not in req.headers["Accept"]

    def oecd_reply(req):
        if "format" in req.url.params or "startPeriod" not in req.url.params:
            return Response(500, text="Internal server error")
        return Response(
            200,
            text="DATAFLOW,FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE\nX,Q,ISR,2026-Q1,1100000.5\nX,Q,ISR,2026-Q2,1110000.0\n",
        )

    oecd = respx.get(url__regex=r"https://sdmx\.oecd\.org/public/rest/data/.*").mock(side_effect=oecd_reply)
    key = "OECD.SDD.NAD,DSD_NAMAIN1@DF_QNA,1.1/Q.Y.ISR.S1.S1.B1GQ._Z._Z._Z.XDC.LR.LA.T0102"
    df = await OecdProvider().get_series(
        SeriesSpec(series_id=f"oecd:{key}", provider="oecd", provider_key=key)
    )
    assert df.height == 2 and df["ts"][-1] == datetime(2026, 4, 1)
    req = oecd.calls[0].request
    assert req.url.params["startPeriod"] == "1950" and req.headers["Accept"].startswith(
        "application/vnd.sdmx.data+csv"
    )
    await OecdProvider().get_series(
        SeriesSpec(series_id=f"oecd:{key}", provider="oecd", provider_key=key), since=date(2025, 5, 1)
    )
    assert oecd.calls[1].request.url.params["startPeriod"] == "2025"


async def test_retired_series_are_disabled(db):
    await load_seed()
    from app.data.catalog.loader import upsert_spec

    await upsert_spec(
        SeriesSpec(
            series_id="estat:prc_hicp_manr/M.RCH_A.CP00.EA20",
            provider="estat",
            provider_key="prc_hicp_manr/M.RCH_A.CP00.EA20",
            name="old HICP",
        )
    )
    await service.ensure_catalog()
    assert (await get_spec("estat:prc_hicp_manr/M.RCH_A.CP00.EA20")).enabled is False
    assert (await get_spec("estat:prc_hicp_minr/M.RCH_A.TOTAL.EA21")).enabled is True
    ea = await service.mapping_for("EA")
    assert ea["inflation"]["cpi"][0] == "estat:prc_hicp_minr/M.RCH_A.TOTAL.EA21"
    assert ea["labour"]["unemployment"][0] == "estat:une_rt_m/M.SA.TOTAL.PC_ACT.T.EA21"


def test_regime_trail_is_consecutive_and_ends_at_the_latest_reading():
    import math

    # a noisy monthly series and a quarterly one; the trail used to skip months (31-day steps) and used a
    # 36-observation window for quarterly data (9 years) while the tiles use 12 quarters (3 years)
    mvals = [2 + math.sin(i / 3) + 0.1 * i for i in range(60)]
    qvals = [1 + math.cos(i) * (1 + i / 10) for i in range(24)]
    m = monthly(date(2021, 9, 1), mvals)
    q = quarterly(date(2020, 10, 1), qvals)
    frames = {"ip": (m, "level", "1mo"), "gdp_yoy": (q, "level", "1q")}
    trail = service._monthly_z_trail(frames, ("ip", "gdp_yoy"))
    months = [p["ts"] for p in trail]
    assert len(months) == 12 and months[-1] == "2026-08-01"
    ym = [int(t[:4]) * 12 + int(t[5:7]) for t in months]
    assert all(b - a == 1 for a, b in zip(ym, ym[1:], strict=False))
    latest = (service._z(mvals, service.Z_WINDOW["1mo"]) + service._z(qvals, service.Z_WINDOW["1q"])) / 2
    assert abs(trail[-1]["z"] - latest) < 1e-9
