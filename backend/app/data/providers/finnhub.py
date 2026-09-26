"""Finnhub adapter — free tier (personal use): 60 calls/min, real-time US quotes, company news, earnings/IPO calendar, peers, metrics.

Free tier does NOT include candles, estimates, price targets or the economic calendar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from app.data.http import get_client
from app.data.providers.base import Capability, CorporateEvent, LicenseSpec, NewsItem, Provider, ProviderError, Quote, RateSpec, SeriesSpec
from app.data.retry import raise_for_retry, retrying

BASE = "https://finnhub.io/api/v1"


@dataclass
class FinnhubProvider(Provider):
    id: str = "finnhub"
    name: str = "Finnhub (free tier)"
    capabilities: Capability = Capability.QUOTES | Capability.NEWS | Capability.EVENTS | Capability.SEARCH
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_second=20, per_minute=55, concurrency=4))
    license: LicenseSpec = field(default_factory=lambda: LicenseSpec(grey=False, personal_use_only=True, note="Finnhub free: personal, non-commercial"))
    requires: tuple[str, ...] = ("finnhub_api_key",)
    api_key: str = ""

    async def _get(self, path: str, **params):
        client = get_client()
        async for attempt in retrying():
            with attempt:
                resp = await client.get(f"{BASE}/{path}", params=params, headers={"X-Finnhub-Token": self.api_key})
                if resp.status_code in (401, 403):
                    raise ProviderError(f"Finnhub {resp.status_code}: {resp.text[:120]}")
                raise_for_retry(resp)
        return resp.json()

    async def get_quotes(self, tickers: list[str]) -> list[Quote]:
        out: list[Quote] = []
        for t in tickers:
            q = await self._get("quote", symbol=t)
            if not q or q.get("c") in (None, 0):
                continue
            ts = datetime.fromtimestamp(q.get("t") or 0, tz=UTC) if q.get("t") else datetime.now(tz=UTC)
            out.append(Quote(ticker=t, ts=ts, last=float(q["c"]), open=q.get("o"), high=q.get("h"), low=q.get("l"), prev_close=q.get("pc"), change_pct=q.get("dp"), source="finnhub"))
        return out

    async def get_news(self, tickers: list[str], since: datetime | None = None) -> list[NewsItem]:
        since = since or datetime.now(tz=UTC) - timedelta(days=7)
        out: list[NewsItem] = []
        for t in tickers:
            items = await self._get("company-news", symbol=t, **{"from": since.date().isoformat(), "to": date.today().isoformat()})
            for it in items or []:
                out.append(
                    NewsItem(
                        id=f"finnhub:{it.get('id')}",
                        source="finnhub",
                        ts=datetime.fromtimestamp(it.get("datetime", 0), tz=UTC),
                        title=it.get("headline", ""),
                        url=it.get("url", ""),
                        tickers=[t],
                        publisher=it.get("source"),
                        summary=it.get("summary"),
                        raw={"category": it.get("category"), "related": it.get("related"), "image": it.get("image")},
                    )
                )
        return out

    async def get_events(self, tickers: list[str], start: date, end: date) -> list[CorporateEvent]:
        out: list[CorporateEvent] = []
        for t in tickers or [None]:
            params = {"from": start.isoformat(), "to": end.isoformat()}
            if t:
                params["symbol"] = t
            data = await self._get("calendar/earnings", **params)
            for e in (data or {}).get("earningsCalendar", []):
                d = e.get("date")
                if not d:
                    continue
                out.append(
                    CorporateEvent(
                        id=f"finnhub:earn:{e.get('symbol')}:{d}",
                        ticker=e.get("symbol", t or ""),
                        kind="earnings",
                        ts=datetime.fromisoformat(d).replace(tzinfo=UTC),
                        source="finnhub",
                        details={k: e.get(k) for k in ("epsEstimate", "epsActual", "revenueEstimate", "revenueActual", "hour", "quarter", "year")},
                    )
                )
        return out

    async def search(self, q: str) -> list[SeriesSpec]:
        data = await self._get("search", q=q)
        out = []
        for r in (data or {}).get("result", [])[:20]:
            sym = r.get("symbol", "")
            out.append(SeriesSpec(series_id=f"finnhub:{sym}:last", provider="finnhub", provider_key=sym, field_name="last", name=r.get("description", sym), freq="tick", unit="USD", value_kind="price", default_transform="log_ret", category="equity"))
        return out

    async def peers(self, ticker: str, grouping: str = "subIndustry") -> list[str]:
        return await self._get("stock/peers", symbol=ticker, grouping=grouping) or []

    async def health(self) -> dict:
        try:
            q = await self._get("quote", symbol="AAPL")
            return {"ok": bool(q and q.get("c")), "aapl": q.get("c") if q else None}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
