"""Digest: items queued outside dispatch windows (quiet hours) are flushed as one Telegram message at 09:00 IL or on demand."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.alerts.channels.telegram import send_telegram
from app.alerts.models import AlertDigest, AlertHistory
from app.data.store.sqlite import session_scope


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


async def queue(text: str, ticker: str = "", history_id: int | None = None, ops: bool = False) -> int:
    async with session_scope() as s:
        row = AlertDigest(history_id=history_id, ticker=ticker, text=text, ops=ops)
        s.add(row)
        await s.flush()
        return row.id


async def pending(limit: int = 200) -> list[dict]:
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(AlertDigest)
                    .where(AlertDigest.flushed_at.is_(None))
                    .order_by(AlertDigest.id)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": r.id,
            "ticker": r.ticker,
            "text": r.text,
            "ops": r.ops,
            "queued_at": r.queued_at,
            "history_id": r.history_id,
        }
        for r in rows
    ]


async def flush(send=None) -> dict:
    """Send all pending items as one message per channel (market / ops). Returns counts. Idempotent."""
    send = send or send_telegram
    items = await pending()
    if not items:
        return {"sent": 0, "items": 0}
    sent = 0
    for ops in (False, True):
        chunk = [i for i in items if i["ops"] == ops]
        if not chunk:
            continue
        header = f"<b>Alert digest</b> · {len(chunk)} item{'s' if len(chunk) != 1 else ''} · {_now():%Y-%m-%d %H:%M} UTC"
        text = header + "\n\n" + "\n\n".join(i["text"] for i in chunk)
        ok = await send(text, ops=ops)
        sent += 1 if ok else 0
        now = _now()
        async with session_scope() as s:
            for i in chunk:
                row = await s.get(AlertDigest, i["id"])
                if row:
                    row.flushed_at = now
                if i["history_id"]:
                    h = await s.get(AlertHistory, i["history_id"])
                    if h:
                        h.delivered = {
                            **(h.delivered or {}),
                            "digest_flushed": ok,
                            "digest_flushed_at": now.isoformat(),
                        }
    return {"sent": sent, "items": len(items)}
