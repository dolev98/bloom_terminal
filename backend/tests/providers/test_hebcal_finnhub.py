from datetime import UTC, datetime

import respx
from httpx import Response

from app.data.providers.finnhub import FinnhubProvider
from app.data.providers.hebcal import HebcalProvider


@respx.mock
async def test_hebcal_marks_tase_closures():
    items = [
        {"title": "Yom Kippur", "date": "2026-09-21", "category": "holiday"},
        {"title": "Pesach II (CH''M)", "date": "2026-04-03", "category": "holiday"},
        {"title": "Erev Pesach", "date": "2026-04-01", "category": "holiday"},
        {"title": "Shabbat Shuva", "date": "2026-09-19", "category": "parashat"},
    ]
    respx.get(url__regex=r"https://www\.hebcal\.com/hebcal.*").mock(
        return_value=Response(200, json={"items": items})
    )
    hs = await HebcalProvider().get_holidays("IL", 2026)
    by = {h.name: h.exchange_closed for h in hs}
    assert by["Yom Kippur"] is True and by["Erev Pesach"] is True and by["Pesach II (CH''M)"] is False
    assert "Shabbat Shuva" not in by


@respx.mock
async def test_finnhub_quote_and_news():
    respx.get("https://finnhub.io/api/v1/quote").mock(
        return_value=Response(
            200,
            json={
                "c": 190.5,
                "o": 189.0,
                "h": 191.0,
                "l": 188.0,
                "pc": 188.5,
                "dp": 1.06,
                "t": 1_700_000_000,
            },
        )
    )
    respx.get("https://finnhub.io/api/v1/company-news").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 1,
                    "datetime": 1_700_000_000,
                    "headline": "Apple news",
                    "url": "https://x/y",
                    "source": "Reuters",
                    "summary": "s",
                }
            ],
        )
    )
    p = FinnhubProvider(api_key="k")
    q = await p.get_quotes(["AAPL"])
    assert q[0].last == 190.5 and q[0].source == "finnhub"
    n = await p.get_news(["AAPL"], since=datetime(2023, 11, 1, tzinfo=UTC))
    assert n[0].title == "Apple news" and n[0].tickers == ["AAPL"]
