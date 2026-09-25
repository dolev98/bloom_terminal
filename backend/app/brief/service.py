"""Morning brief: composes calendar, watchlist moves, news, valuation upside and alert hits into one page + Telegram."""

from __future__ import annotations

import html
import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.alerts.channels.telegram import send_telegram
from app.data.store.models import Pref
from app.data.store.sqlite import session_scope
from app.market import service as market

log = logging.getLogger(__name__)
IL = ZoneInfo("Asia/Jerusalem")
PREF_KEY = "brief_last_sent"


async def _watchlist_tickers() -> list[str]:
    from app.api.routers.watchlists import all_tickers

    return await all_tickers()


async def _calendar_today(tickers: list[str]) -> list[dict]:
    try:
        from app.calendar import service as cal

        today = datetime.now(tz=IL).date()
        items = await cal.list_events(today, today + timedelta(days=1), None, None, 2, False, None)
        return items[:20]
    except Exception as e:  # pragma: no cover - module optional
        log.debug("brief calendar failed: %s", e)
        return []


async def _news(tickers: list[str]) -> list[dict]:
    try:
        from app.news import service as news

        items = await news.feed(tickers=tickers, since="24h", min_importance=40, limit=12)
        return items
    except Exception as e:  # pragma: no cover
        log.debug("brief news failed: %s", e)
        return []


async def _upside(tickers: list[str]) -> list[dict]:
    out = []
    try:
        from app.valuation import service as val

        for t in tickers:
            rows = await val.latest_fair_values(t)
            base = next(
                (r for r in rows if r.get("model") == "fcff" and r.get("scenario", "base") == "base"), None
            ) or (rows[0] if rows else None)
            if base and base.get("upside") is not None:
                out.append(
                    {
                        "ticker": t,
                        "fair_value": base.get("value"),
                        "price": base.get("price"),
                        "upside": base.get("upside"),
                        "model": base.get("model"),
                    }
                )
    except Exception as e:  # pragma: no cover
        log.debug("brief valuation failed: %s", e)
    return sorted(out, key=lambda r: -(r["upside"] or 0))


async def _alerts_24h() -> list[dict]:
    try:
        from app.alerts.models import AlertHistory

        since = datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(hours=24)
        async with session_scope() as s:
            rows = (
                (
                    await s.execute(
                        select(AlertHistory)
                        .where(AlertHistory.fired_at >= since)
                        .order_by(AlertHistory.fired_at.desc())
                        .limit(20)
                    )
                )
                .scalars()
                .all()
            )
        return [
            {
                "ticker": r.ticker,
                "fired_at": r.fired_at,
                "value": r.value,
                "threshold": r.threshold,
                "rule_id": r.rule_id,
            }
            for r in rows
        ]
    except Exception as e:  # pragma: no cover
        log.debug("brief alerts failed: %s", e)
        return []


def _moves(tickers: list[str]) -> list[dict]:
    qs = market.cached_quotes(tickers)
    rows = [
        {
            "ticker": q["ticker"],
            "last": q["last"],
            "change_pct": q.get("change_pct"),
            "source": q.get("source"),
            "stale": q.get("stale"),
        }
        for q in qs
        if q.get("change_pct") is not None
    ]
    return sorted(rows, key=lambda r: -abs(r["change_pct"]))


async def build_brief() -> dict:
    tickers = await _watchlist_tickers()
    now = datetime.now(tz=IL)
    return {
        "date": now.date().isoformat(),
        "generated_at": now.isoformat(),
        "watchlist": tickers,
        "moves": _moves(tickers)[:12],
        "overnight": _moves(
            ["SPY", "QQQ", "IWM", "^VIX", "TLT", "GC=F", "CL=F", "BTC-USD", "ILS=X", "TA35.TA"]
        ),
        "calendar": await _calendar_today(tickers),
        "news": await _news(tickers),
        "upside": (await _upside(tickers))[:10],
        "alerts": await _alerts_24h(),
    }


def render_telegram(b: dict) -> str:
    esc = html.escape
    lines = [f"<b>Morning brief · {b['date']}</b>"]
    if b["overnight"]:
        lines.append("\n<b>Overnight</b>")
        lines.append(" · ".join(f"{esc(r['ticker'])} {r['change_pct']:+.1f}%" for r in b["overnight"][:8]))
    if b["moves"]:
        lines.append("\n<b>Watchlist movers</b>")
        for r in b["moves"][:6]:
            lines.append(f"{esc(r['ticker'])} {r['last']:.2f} ({r['change_pct']:+.1f}%)")
    if b["calendar"]:
        lines.append("\n<b>Today</b>")
        for e in b["calendar"][:8]:
            ts = str(e.get("release_ts") or "")[11:16]
            lines.append(f"{ts} {esc(str(e.get('country') or ''))} {esc(str(e.get('title') or ''))[:60]}")
    if b["upside"]:
        lines.append("\n<b>Upside vs fair value</b>")
        for r in b["upside"][:6]:
            # upside is a ratio (-0.65 = -65%)
            lines.append(f"{esc(r['ticker'])} {r['upside'] * 100:+.0f}% ({r['model']})")
    if b["alerts"]:
        lines.append(f"\n<b>Alerts (24h)</b>: {len(b['alerts'])}")
    if b["news"]:
        lines.append("\n<b>News</b>")
        for n in b["news"][:6]:
            t = n.get("title") or n.get("summary") or ""
            lines.append(f"• [{n.get('importance', 0)}] {esc(str(t))[:90]}")
    return "\n".join(lines)


async def send_brief(force: bool = False) -> dict:
    today = date.today().isoformat()
    async with session_scope() as s:
        row = await s.get(Pref, PREF_KEY)
        if row and row.value == today and not force:
            return {"sent": False, "reason": "already sent today"}
    b = await build_brief()
    ok = await send_telegram(render_telegram(b))
    async with session_scope() as s:
        row = await s.get(Pref, PREF_KEY)
        if row is None:
            s.add(Pref(key=PREF_KEY, value=today))
        else:
            row.value = today
    return {
        "sent": ok,
        "date": today,
        "sections": {
            k: len(v) if isinstance(v, list) else v
            for k, v in b.items()
            if k in ("moves", "calendar", "news", "upside", "alerts")
        },
    }


async def brief_job() -> dict:
    """06:30 IL; also called by the wake catch-up when the day's brief was missed."""
    now = datetime.now(tz=IL)
    if now.hour < 6:
        return {"sent": False, "reason": "too early"}
    return await send_brief()
