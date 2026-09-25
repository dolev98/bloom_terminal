"""Alert engine: evaluate rules against cached quotes + latest fair values, with a staleness guard, grey-quote gate,
armed->fired state machine (hysteresis re-arm), cooldown, daily cap, quiet-hours digest and Telegram/in-app dispatch.

`evaluate_all(trigger, now=None)` is the single entry point (job every 60 s in market hours, POST /api/alerts/evaluate).
Tests monkeypatch `app.alerts.engine.send_telegram`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.alerts import digest as digest_mod
from app.alerts import rules as rules_mod
from app.alerts.channels.telegram import send_telegram
from app.alerts.models import AlertHistory, AlertRule, AlertState
from app.analytics.market_hours import tase_market_open, us_market_open
from app.data.store.sqlite import session_scope

log = logging.getLogger(__name__)
IL = ZoneInfo("Asia/Jerusalem")
QUOTE_MAX_AGE_S = 15 * 60
DEFAULT_QUIET = {"start": "22:00", "end": "07:00"}


def _now() -> datetime:
    return datetime.now(tz=UTC)


def in_quiet_hours(qh: dict | None, now: datetime) -> bool:
    qh = qh or DEFAULT_QUIET
    if not qh or not qh.get("start") or not qh.get("end"):
        return False
    local = now.astimezone(IL).time()
    h1, m1 = map(int, str(qh["start"]).split(":"))
    h2, m2 = map(int, str(qh["end"]).split(":"))
    start, end = time(h1, m1), time(h2, m2)
    if start <= end:
        return start <= local < end
    return local >= start or local < end  # overnight window


def quote_age_s(quote: dict | None, now: datetime) -> float | None:
    if not quote:
        return None
    ts = quote.get("ts")
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (now - ts).total_seconds()


async def _tickers_for(rule: AlertRule) -> list[str]:
    if rule.rule_type == "series_threshold":
        return [rule.ticker or "ALL"]
    if rule.ticker and rule.ticker.upper() != "ALL":
        return [rule.ticker.upper()]
    from app.api.routers.watchlists import all_tickers
    from app.valuation.service import tickers_with_fair_values

    return sorted(set(await all_tickers()) | set(await tickers_with_fair_values()))


async def _state(s, rule_id: int, ticker: str) -> AlertState:
    st = (
        await s.execute(select(AlertState).where(AlertState.rule_id == rule_id, AlertState.ticker == ticker))
    ).scalar_one_or_none()
    if st is None:
        st = AlertState(rule_id=rule_id, ticker=ticker, state="armed")
        s.add(st)
        await s.flush()
    return st


def market_open_for(ticker: str, now: datetime) -> bool:
    """Is the ticker's own exchange in session? (A US quote from last night's close is not stale just because
    the TASE is open.)"""
    return tase_market_open(now) if ticker.upper().endswith(".TA") else us_market_open(now)


async def _note_skip(rule_id: int, ticker: str, now: datetime, reason: str | None) -> None:
    """Remember why the last check was skipped ('skip: <reason>') so the UI can say it in words; the armed/
    fired state and the last value are left untouched."""
    async with session_scope() as s:
        st = await _state(s, rule_id, ticker)
        st.last_eval_at = now.astimezone(UTC).replace(tzinfo=None)
        st.last_reason = f"skip: {reason or 'no value'}"[:200]


async def evaluate_rule(rule: AlertRule, ticker: str, now: datetime, trigger: str = "manual") -> dict:
    """Evaluate one (rule, ticker). Returns a small dict describing what happened (for the API / logs)."""
    from app.data.series_service import read_series
    from app.market.service import cached_quotes
    from app.valuation.service import reference_value

    ticker = ticker.upper()
    quote = (cached_quotes([ticker]) or [None])[0] if rule.rule_type != "series_threshold" else None
    fv = analyst = reverse = None
    series_last = None
    if rule.rule_type in ("upside_gt", "price_below_fv", "mos_gt", "price_crosses_fv"):
        fv = await reference_value(
            ticker, rule.reference or "base_dcf", (rule.params or {}).get("scenario", "base")
        )
    elif rule.rule_type == "consensus_gap_gt":
        analyst = await reference_value(ticker, "analyst")
    elif rule.rule_type == "implied_growth_lt":
        reverse = await reference_value(ticker, "reverse_dcf")
    elif rule.rule_type == "series_threshold" and rule.series_id:
        df = read_series(rule.series_id)
        if not df.is_empty():
            series_last = float(df.drop_nulls().tail(1)["value"][0]) if df.drop_nulls().height else None
    m = rules_mod.compute_metric(rule.rule_type, quote, fv, series_last, analyst, reverse)
    out = {
        "rule_id": rule.id,
        "ticker": ticker,
        "value": m.value,
        "state": None,
        "action": "skip",
        "reason": m.reason,
    }
    if not m.ok or m.value is None:
        await _note_skip(rule.id, ticker, now, out["reason"])
        return out
    # --- staleness / grey guards (quotes only) ---
    if quote is not None:
        age = quote_age_s(quote, now)
        if age is not None and age > QUOTE_MAX_AGE_S and market_open_for(ticker, now):
            log.warning(
                "alert %s/%s: stale quote (%.0f s) during market hours; suppressed", rule.id, ticker, age
            )
            out["reason"] = f"stale quote {int(age)}s"
            await _note_skip(rule.id, ticker, now, out["reason"])
            return out
        if str(quote.get("source", "")).startswith("yf") and not (rule.params or {}).get("allow_grey"):
            out["reason"] = "grey-only quote (yfinance); rule does not allow_grey"
            await _note_skip(rule.id, ticker, now, out["reason"])
            return out
    direction = rules_mod.direction_for(rule.rule_type, rule.params)
    hyst = rule.hysteresis_pp if rule.hysteresis_pp is not None else 5.0
    naive_now = now.astimezone(UTC).replace(tzinfo=None)
    today = now.astimezone(IL).date()
    async with session_scope() as s:
        st = await _state(s, rule.id, ticker)
        prev_value = st.last_value
        st.last_value = m.value
        st.last_eval_at = naive_now
        if st.fired_day != today:
            st.fired_day, st.fired_today = today, 0
        out["state"] = st.state
        if st.state == "fired":
            if rules_mod.rearmed(direction, m.value, rule.threshold, hyst):
                st.state = "armed"
                st.last_reason = "re-armed"
                out.update(state="armed", action="rearm")
            else:
                out.update(action="hold", reason="fired; waiting for hysteresis re-arm")
            return out
        if not rules_mod.crossed(direction, m.value, rule.threshold, prev_value):
            out.update(action="none")
            return out
        if st.last_fired_at and naive_now - st.last_fired_at < timedelta(hours=rule.cooldown_hours or 0):
            out.update(
                action="cooldown",
                reason=f"cooldown until {(st.last_fired_at + timedelta(hours=rule.cooldown_hours)).isoformat()}",
            )
            st.last_reason = "cooldown"
            return out
        if rule.daily_cap and st.fired_today >= rule.daily_cap:
            out.update(action="capped", reason=f"daily cap {rule.daily_cap} reached")
            st.last_reason = "daily cap"
            return out
        st.state = "fired"
        st.last_fired_at = naive_now
        st.fired_today += 1
        st.last_reason = f"fired ({trigger})"
        out.update(state="fired", action="fire")
    delivered = await dispatch(rule, ticker, m, now, trigger)
    out["delivered"] = delivered
    return out


async def dispatch(rule: AlertRule, ticker: str, m: rules_mod.Metric, now: datetime, trigger: str) -> dict:
    """Always writes alert_history (in-app inbox); Telegram/ops immediately, or queued to the digest in quiet hours."""
    text = rules_mod.format_message(rule, ticker, m, rule.reference)
    channels = list(rule.channels or ["inapp"])
    delivered: dict = {"inapp": True}
    price_ts = m.price_ts
    if isinstance(price_ts, str):
        price_ts = datetime.fromisoformat(price_ts)
    if isinstance(price_ts, datetime) and price_ts.tzinfo:
        price_ts = price_ts.astimezone(UTC).replace(tzinfo=None)
    async with session_scope() as s:
        h = AlertHistory(
            rule_id=rule.id,
            ticker=ticker,
            fired_at=now.astimezone(UTC).replace(tzinfo=None),
            rule_type=rule.rule_type,
            value=m.value,
            threshold=rule.threshold,
            price=m.price,
            price_source=m.price_source,
            price_ts=price_ts,
            fair_value=m.fair_value,
            message=text,
            payload={
                "reference": rule.reference,
                "trigger": trigger,
                "extra": m.extra,
                "channels": channels,
                "hysteresis_pp": rule.hysteresis_pp,
            },
        )
        s.add(h)
        await s.flush()
        hid = h.id
    quiet = in_quiet_hours(rule.quiet_hours, now)
    for ch in channels:
        if ch not in ("telegram", "ops"):
            continue
        ops = ch == "ops"
        if quiet:
            await digest_mod.queue(text, ticker, hid, ops=ops)
            delivered[ch] = "digest"
        else:
            try:
                delivered[ch] = bool(await send_telegram(text, ops=ops))
            except Exception as e:  # never let a channel failure break the engine
                log.warning("telegram dispatch failed: %s", e)
                delivered[ch] = False
    async with session_scope() as s:
        h = await s.get(AlertHistory, hid)
        if h:
            h.delivered = delivered
    return {**delivered, "history_id": hid}


async def evaluate_all(trigger: str = "job", now: datetime | None = None) -> dict:
    now = now or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    async with session_scope() as s:
        rules = (
            (await s.execute(select(AlertRule).where(AlertRule.enabled.is_(True)).order_by(AlertRule.id)))
            .scalars()
            .all()
        )
    results: list[dict] = []
    for rule in rules:
        try:
            tickers = await _tickers_for(rule)
        except Exception as e:
            log.warning("rule %s: cannot expand tickers: %s", rule.id, e)
            continue
        for t in tickers:
            try:
                results.append(await evaluate_rule(rule, t, now, trigger))
            except Exception as e:
                log.exception("rule %s/%s failed", rule.id, t)
                results.append(
                    {
                        "rule_id": rule.id,
                        "ticker": t,
                        "action": "error",
                        "reason": f"{type(e).__name__}: {e}"[:200],
                    }
                )
    fired = [r for r in results if r.get("action") == "fire"]
    return {
        "trigger": trigger,
        "rules": len(rules),
        "evaluated": len(results),
        "fired": len(fired),
        "results": results,
    }


async def states() -> list[dict]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(AlertState, AlertRule)
                .join(AlertRule, AlertRule.id == AlertState.rule_id)
                .order_by(AlertState.rule_id, AlertState.ticker)
            )
        ).all()
    return [
        {
            "rule_id": st.rule_id,
            "rule_name": r.name,
            "rule_type": r.rule_type,
            "ticker": st.ticker,
            "state": st.state,
            "last_value": st.last_value,
            "threshold": r.threshold,
            "last_eval_at": st.last_eval_at,
            "last_fired_at": st.last_fired_at,
            "fired_today": st.fired_today,
            "last_reason": st.last_reason,
        }
        for st, r in rows
    ]
