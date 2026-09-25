from datetime import UTC, date, datetime, timedelta

import pytest

from app.alerts import engine
from app.alerts.models import AlertHistory, AlertRule
from app.data.providers.base import Quote
from app.data.store.sqlite import session_scope
from app.market import service as market_service
from app.valuation.orm import FairValueDaily

OPEN = datetime(2026, 9, 23, 15, 0, tzinfo=UTC)  # Wednesday 11:00 ET (US open), 18:00 IL
CLOSED_QUIET = datetime(2026, 9, 23, 20, 30, tzinfo=UTC)  # 16:30 ET closed, 23:30 IL quiet hours


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    async def fake_send(text, ops=False, parse_mode="HTML"):
        calls.append({"text": text, "ops": ops})
        return True

    monkeypatch.setattr(engine, "send_telegram", fake_send)
    return calls


def _quote(ticker="AAPL", last=100.0, source="finnhub", ts=None, change_pct=None):
    market_service._quotes[ticker] = Quote(
        ticker=ticker, ts=ts or OPEN, last=last, source=source, change_pct=change_pct
    )


async def _fv(ticker="AAPL", value=130.0, model="fcff"):
    async with session_scope() as s:
        s.add(
            FairValueDaily(
                ticker=ticker,
                date=date(2026, 9, 22),
                model=model,
                scenario="base",
                value=value,
                price=100.0,
                price_source="finnhub",
            )
        )


async def _rule(**kw) -> AlertRule:
    async with session_scope() as s:
        r = AlertRule(
            **{
                "name": "upside",
                "ticker": "AAPL",
                "rule_type": "upside_gt",
                "reference": "base_dcf",
                "threshold": 25.0,
                "hysteresis_pp": 5.0,
                "cooldown_hours": 24.0,
                "daily_cap": 3,
                "channels": ["telegram", "inapp"],
                "quiet_hours": {"start": "00:00", "end": "00:00"},
                **kw,
            }
        )
        s.add(r)
        await s.flush()
        return r


async def _history():
    from sqlalchemy import select

    async with session_scope() as s:
        return (await s.execute(select(AlertHistory).order_by(AlertHistory.id))).scalars().all()


async def test_state_machine_fire_cooldown_rearm(db, sent):
    rule = await _rule()
    await _fv(value=130.0)  # upside 30% > 25
    _quote(last=100.0)
    r1 = await engine.evaluate_all("test", now=OPEN)
    assert r1["fired"] == 1 and r1["results"][0]["action"] == "fire" and r1["results"][0]["state"] == "fired"
    assert (
        len(sent) == 1
        and "<b>AAPL</b>" in sent[0]["text"]
        and "30.00%" in sent[0]["text"]
        and "130.00" in sent[0]["text"]
    )
    hist = await _history()
    assert (
        len(hist) == 1
        and hist[0].delivered["telegram"] is True
        and hist[0].delivered["inapp"] is True
        and hist[0].rule_id == rule.id
    )
    # still above threshold -> hold (no double fire)
    r2 = await engine.evaluate_all("test", now=OPEN + timedelta(minutes=1))
    assert r2["fired"] == 0 and r2["results"][0]["action"] == "hold"
    # 22% upside: between threshold-hysteresis (20) and threshold -> still fired
    _quote(last=106.6)
    r3 = await engine.evaluate_all("test", now=OPEN + timedelta(minutes=2))
    assert r3["results"][0]["action"] == "hold" and r3["results"][0]["state"] == "fired"
    # 18% upside -> re-arm
    _quote(last=110.0)
    r4 = await engine.evaluate_all("test", now=OPEN + timedelta(minutes=3))
    assert r4["results"][0]["action"] == "rearm" and r4["results"][0]["state"] == "armed"
    # crossing again inside the 24h cooldown -> suppressed
    _quote(last=100.0)
    r5 = await engine.evaluate_all("test", now=OPEN + timedelta(minutes=4))
    assert r5["results"][0]["action"] == "cooldown" and len(sent) == 1
    # after the cooldown -> fires again
    _quote(last=100.0, ts=OPEN + timedelta(hours=25))
    r6 = await engine.evaluate_all("test", now=OPEN + timedelta(hours=25))
    assert r6["fired"] == 1 and len(sent) == 2
    states = await engine.states()
    assert states[0]["state"] == "fired" and states[0]["fired_today"] == 1  # new IL day


async def test_staleness_guard_and_grey_gate(db, sent):
    await _rule(name="stale")
    await _fv(value=130.0)
    _quote(last=100.0, ts=OPEN - timedelta(minutes=20))  # 20 min old during market hours
    r = await engine.evaluate_all("test", now=OPEN)
    assert r["fired"] == 0 and "stale" in r["results"][0]["reason"] and not sent
    _quote(last=100.0, ts=OPEN, source="yf")  # grey-only quote
    r = await engine.evaluate_all("test", now=OPEN)
    assert r["fired"] == 0 and "grey" in r["results"][0]["reason"]
    # outside market hours an old quote is fine (EOD data) — closed at 20:30 UTC; quiet hours handled separately
    _quote(last=100.0, ts=OPEN - timedelta(hours=3), source="finnhub")
    r = await engine.evaluate_all("test", now=CLOSED_QUIET)
    assert r["fired"] == 1


async def test_allow_grey_param(db, sent):
    await _rule(params={"allow_grey": True})
    await _fv(value=130.0)
    _quote(last=100.0, source="yf")
    r = await engine.evaluate_all("test", now=OPEN)
    assert r["fired"] == 1


async def test_quiet_hours_queue_to_digest_and_flush(db, sent):
    await _rule(quiet_hours=None)  # default 22:00-07:00 IL
    await _fv(value=130.0)
    _quote(last=100.0, ts=CLOSED_QUIET)
    r = await engine.evaluate_all("test", now=CLOSED_QUIET)
    assert r["fired"] == 1 and not sent  # queued, not sent
    hist = await _history()
    assert hist[0].delivered["telegram"] == "digest"
    from app.alerts import digest

    pending = await digest.pending()
    assert len(pending) == 1 and pending[0]["ticker"] == "AAPL"
    out = await digest.flush(send=engine.send_telegram)
    assert out == {"sent": 1, "items": 1} and len(sent) == 1 and "Alert digest" in sent[0]["text"]
    assert await digest.pending() == []
    assert (await digest.flush(send=engine.send_telegram))["items"] == 0


async def test_daily_cap(db, sent):
    await _rule(cooldown_hours=0, daily_cap=2)
    await _fv(value=130.0)
    t = OPEN
    for i, last in enumerate([100.0, 115.0, 100.0, 115.0, 100.0, 115.0]):
        _quote(last=last, ts=t + timedelta(minutes=i))
        await engine.evaluate_all("test", now=t + timedelta(minutes=i))
    assert len(sent) == 2
    states = await engine.states()
    assert states[0]["fired_today"] == 2 and states[0]["last_reason"] == "daily cap"


async def test_other_rule_types(db, sent):
    import polars as pl

    from app.data.series_service import write_manual

    await _fv(value=130.0, model="analyst")
    await _fv(value=90.0, model="multiples")
    await _rule(name="price", rule_type="price_above", threshold=99.0, channels=["inapp"])
    await _rule(name="pct", rule_type="pct_change_gt", threshold=3.0, channels=["ops"])
    await _rule(name="cons", rule_type="consensus_gap_gt", threshold=20.0)
    await _rule(name="mos", rule_type="mos_gt", reference="multiples", threshold=5.0)
    await _rule(name="cross", rule_type="price_crosses_fv", reference="multiples", threshold=0.0)
    write_manual(
        "fred:DGS10",
        pl.DataFrame({"ts": [datetime(2026, 9, 20)], "value": [4.5]}).with_columns(
            pl.col("ts").cast(pl.Datetime("us"))
        ),
    )
    await _rule(
        name="series",
        rule_type="series_threshold",
        series_id="fred:DGS10",
        ticker="ALL",
        threshold=4.0,
        channels=["inapp"],
    )
    _quote(last=100.0, change_pct=-4.2)
    r = await engine.evaluate_all("test", now=OPEN)
    by = {x["rule_id"]: x for x in r["results"]}
    actions = [by[k]["action"] for k in sorted(by)]
    assert actions == [
        "fire",
        "fire",
        "fire",
        "none",
        "none",
        "fire",
    ]  # price, pct, consensus, mos (price 100 > fv 90), cross (needs a previous value), series
    assert sent and sent[0]["ops"] is True and "4.20%" in sent[0]["text"]
    _quote(last=85.0, ts=OPEN + timedelta(minutes=1))  # crosses below multiples fv 90 and mos = 5.9% > 5
    r = await engine.evaluate_all("test", now=OPEN + timedelta(minutes=1))
    by = {x["rule_id"]: x for x in r["results"]}
    assert by[4]["action"] == "fire" and by[5]["action"] == "fire"


async def test_staleness_uses_the_tickers_own_market_and_skip_reason_is_kept(db, sent):
    await _rule(name="us-only")
    await _fv(value=130.0)
    # Thursday 10:30 IL: TASE open, US closed -> last night's US close is not "stale"
    tase_open_us_closed = datetime(2026, 9, 24, 7, 30, tzinfo=UTC)
    _quote(last=100.0, ts=datetime(2026, 9, 23, 20, 0, tzinfo=UTC))
    r = await engine.evaluate_all("test", now=tase_open_us_closed)
    assert "stale" not in (r["results"][0].get("reason") or "")
    # during US hours the same old quote is skipped, and the reason is stored for the UI
    _quote(last=100.0, ts=OPEN - timedelta(minutes=20))
    r = await engine.evaluate_all("test", now=OPEN)
    assert r["results"][0]["action"] == "skip"
    st = (await engine.states())[0]
    assert st["last_reason"].startswith("skip: stale quote") and st["last_value"] is not None
