"""/api/alerts — rules CRUD, state, history inbox (ack/snooze), evaluate now, digest flush, rule-type schema."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.alerts import digest as digest_mod
from app.alerts import engine
from app.alerts.models import CHANNELS, REFERENCES, RULE_TYPES, AlertHistory, AlertRule, AlertState
from app.alerts.rules import RULE_SCHEMA
from app.data.store.sqlite import session_scope

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _rule(r: AlertRule) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "ticker": r.ticker,
        "rule_type": r.rule_type,
        "reference": r.reference,
        "series_id": r.series_id,
        "threshold": r.threshold,
        "hysteresis_pp": r.hysteresis_pp,
        "cooldown_hours": r.cooldown_hours,
        "daily_cap": r.daily_cap,
        "channels": r.channels,
        "quiet_hours": r.quiet_hours,
        "params": r.params,
        "enabled": r.enabled,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


def _hist(h: AlertHistory) -> dict:
    return {
        "id": h.id,
        "rule_id": h.rule_id,
        "ticker": h.ticker,
        "fired_at": h.fired_at,
        "rule_type": h.rule_type,
        "value": h.value,
        "threshold": h.threshold,
        "price": h.price,
        "price_source": h.price_source,
        "price_ts": h.price_ts,
        "fair_value": h.fair_value,
        "message": h.message,
        "payload": h.payload,
        "delivered": h.delivered,
        "acknowledged_at": h.acknowledged_at,
        "snoozed_until": h.snoozed_until,
    }


class RuleBody(BaseModel):
    name: str = ""
    ticker: str = "ALL"
    rule_type: str
    reference: str = "base_dcf"
    series_id: str | None = None
    threshold: float = 25.0
    hysteresis_pp: float = 5.0
    cooldown_hours: float = 24.0
    daily_cap: int = 3
    channels: list[str] = Field(default_factory=lambda: ["telegram", "inapp"])
    quiet_hours: dict[str, str] | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    def validate_rule(self) -> None:
        if self.rule_type not in RULE_TYPES:
            raise HTTPException(422, f"rule_type must be one of {RULE_TYPES}")
        if self.rule_type == "series_threshold" and not self.series_id:
            raise HTTPException(422, "series_threshold needs series_id")
        if not (self.reference in REFERENCES or self.reference.startswith("model:")):
            raise HTTPException(422, f"reference must be one of {REFERENCES} or model:<id>")
        bad = [c for c in self.channels if c not in CHANNELS]
        if bad:
            raise HTTPException(422, f"unknown channels {bad}")


@router.get("/rule-types")
async def rule_types() -> dict:
    return {
        "rule_types": RULE_SCHEMA,
        "references": list(REFERENCES) + ["model:<id>"],
        "channels": list(CHANNELS),
        "defaults": {
            "hysteresis_pp": 5.0,
            "cooldown_hours": 24.0,
            "daily_cap": 3,
            "quiet_hours": engine.DEFAULT_QUIET,
        },
    }


@router.get("/rules")
async def list_rules() -> list[dict]:
    async with session_scope() as s:
        rows = (await s.execute(select(AlertRule).order_by(AlertRule.id))).scalars().all()
    return [_rule(r) for r in rows]


@router.post("/rules")
async def create_rule(body: RuleBody) -> dict:
    body.validate_rule()
    async with session_scope() as s:
        r = AlertRule(**{**body.model_dump(), "ticker": body.ticker.upper() or "ALL"})
        s.add(r)
        await s.flush()
        out = _rule(r)
    return out


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: int, body: RuleBody) -> dict:
    body.validate_rule()
    async with session_scope() as s:
        r = await s.get(AlertRule, rule_id)
        if r is None:
            raise HTTPException(404, "rule not found")
        for k, v in body.model_dump().items():
            setattr(r, k, v.upper() if k == "ticker" else v)
        await s.flush()
        out = _rule(r)
    return out


@router.patch("/rules/{rule_id}")
async def patch_rule(rule_id: int, body: dict[str, Any]) -> dict:
    async with session_scope() as s:
        r = await s.get(AlertRule, rule_id)
        if r is None:
            raise HTTPException(404, "rule not found")
        for k, v in body.items():
            if k in ("id", "created_at"):
                continue
            if hasattr(r, k):
                setattr(r, k, v)
        await s.flush()
        out = _rule(r)
    return out


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int) -> dict:
    async with session_scope() as s:
        r = await s.get(AlertRule, rule_id)
        if r is None:
            raise HTTPException(404, "rule not found")
        await s.execute(AlertState.__table__.delete().where(AlertState.rule_id == rule_id))
        await s.delete(r)
    return {"deleted": True}


@router.get("/state")
async def state() -> list[dict]:
    return await engine.states()


@router.get("/history")
async def history(
    limit: int = Query(100, le=1000), ticker: str | None = None, unacked: bool = False
) -> list[dict]:
    async with session_scope() as s:
        stmt = select(AlertHistory)
        if ticker:
            stmt = stmt.where(AlertHistory.ticker == ticker.upper())
        if unacked:
            stmt = stmt.where(AlertHistory.acknowledged_at.is_(None))
        rows = (await s.execute(stmt.order_by(AlertHistory.id.desc()).limit(limit))).scalars().all()
    return [_hist(h) for h in rows]


@router.post("/history/{hid}/ack")
async def ack(hid: int) -> dict:
    async with session_scope() as s:
        h = await s.get(AlertHistory, hid)
        if h is None:
            raise HTTPException(404, "not found")
        h.acknowledged_at = datetime.now(tz=UTC).replace(tzinfo=None)
        out = _hist(h)
    return out


class SnoozeBody(BaseModel):
    hours: float = 24.0


@router.post("/history/{hid}/snooze")
async def snooze(hid: int, body: SnoozeBody | None = None) -> dict:
    hours = body.hours if body else 24.0
    async with session_scope() as s:
        h = await s.get(AlertHistory, hid)
        if h is None:
            raise HTTPException(404, "not found")
        h.snoozed_until = datetime.now(tz=UTC).replace(tzinfo=None) + timedelta(hours=hours)
        out = _hist(h)
    return out


@router.post("/evaluate")
async def evaluate() -> dict:
    return await engine.evaluate_all("manual")


@router.get("/digest")
async def digest_pending() -> list[dict]:
    return await digest_mod.pending()


@router.post("/digest/flush")
async def digest_flush() -> dict:
    return await digest_mod.flush()
