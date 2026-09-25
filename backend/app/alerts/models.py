"""ORM tables for alerts: rules, per-rule state machine, history (in-app inbox) and the off-hours digest queue."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.store.models import utcnow
from app.data.store.sqlite import Base

RULE_TYPES = (
    "upside_gt",
    "price_below_fv",
    "price_crosses_fv",
    "mos_gt",
    "consensus_gap_gt",
    "implied_growth_lt",
    "price_above",
    "price_below",
    "pct_change_gt",
    "series_threshold",
)
REFERENCES = ("base_dcf", "imported_fv", "multiples", "blended", "analyst")
CHANNELS = ("telegram", "inapp", "ops")


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    ticker: Mapped[str] = mapped_column(
        String(32), default="ALL", index=True
    )  # 'ALL' = every watchlist / valued ticker
    rule_type: Mapped[str] = mapped_column(String(32))
    reference: Mapped[str] = mapped_column(
        String(64), default="base_dcf"
    )  # base_dcf|imported_fv|multiples|blended|model:<id>
    series_id: Mapped[str | None] = mapped_column(String(200), nullable=True)  # for series_threshold
    threshold: Mapped[float] = mapped_column(Float, default=25.0)
    hysteresis_pp: Mapped[float] = mapped_column(Float, default=5.0)
    cooldown_hours: Mapped[float] = mapped_column(Float, default=24.0)
    daily_cap: Mapped[int] = mapped_column(Integer, default=3)
    channels: Mapped[list] = mapped_column(JSON, default=lambda: ["telegram", "inapp"])
    quiet_hours: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # {"start": "22:00", "end": "07:00"} IL time
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # allow_grey, direction, scenario, note
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AlertState(Base):
    """State machine per (rule, ticker): armed -> fired on crossing; re-armed when metric backs off by hysteresis."""

    __tablename__ = "alert_state"
    __table_args__ = (Index("ix_alert_state_rule_ticker", "rule_id", "ticker", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(32), default="")
    state: Mapped[str] = mapped_column(String(12), default="armed")  # armed | fired
    last_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_eval_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(nullable=True)
    fired_today: Mapped[int] = mapped_column(Integer, default=0)
    fired_day: Mapped[date | None] = mapped_column(nullable=True)
    last_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)


class AlertHistory(Base):
    __tablename__ = "alert_history"
    __table_args__ = (Index("ix_alert_history_fired", "fired_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rule_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    fired_at: Mapped[datetime] = mapped_column(default=utcnow)
    rule_type: Mapped[str] = mapped_column(String(32), default="")
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    price_ts: Mapped[datetime | None] = mapped_column(nullable=True)
    fair_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # full metric context for replay
    delivered: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # {"telegram": true, "digest": true, "inapp": true}
    acknowledged_at: Mapped[datetime | None] = mapped_column(nullable=True)
    snoozed_until: Mapped[datetime | None] = mapped_column(nullable=True)


class AlertDigest(Base):
    """Items queued during quiet hours / outside market hours; flushed as one Telegram message at 09:00 IL or on demand."""

    __tablename__ = "alert_digest"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    history_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ticker: Mapped[str] = mapped_column(String(32), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    ops: Mapped[bool] = mapped_column(Boolean, default=False)
    queued_at: Mapped[datetime] = mapped_column(default=utcnow)
    flushed_at: Mapped[datetime | None] = mapped_column(nullable=True)
