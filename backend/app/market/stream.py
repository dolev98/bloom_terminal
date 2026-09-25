"""Finnhub trade websocket (free tier, ≤50 symbols) → quote cache → terminal WS hub. Reconnects with backoff."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from app.core.config import get_settings
from app.data.providers.base import Quote
from app.market import service

log = logging.getLogger(__name__)
_task: asyncio.Task | None = None
_symbols: set[str] = set()
_ws = None


def wanted_symbols() -> set[str]:
    return set(_symbols)


async def set_symbols(symbols: list[str]) -> None:
    global _symbols
    new = {s.upper() for s in symbols if service.is_us_symbol(s)}
    new = set(sorted(new)[:50])
    added, removed = new - _symbols, _symbols - new
    _symbols = new
    if _ws is not None:
        for s in removed:
            await _send({"type": "unsubscribe", "symbol": s})
        for s in added:
            await _send({"type": "subscribe", "symbol": s})


async def _send(msg: dict) -> None:
    try:
        if _ws is not None:
            await _ws.send(json.dumps(msg))
    except Exception as e:
        log.debug("ws send failed: %s", e)


async def _run() -> None:
    global _ws
    import websockets

    s = get_settings()
    backoff = 5
    while True:
        if not s.finnhub_api_key or not _symbols:
            await asyncio.sleep(15)
            continue
        url = f"wss://ws.finnhub.io?token={s.finnhub_api_key}"
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                _ws = ws
                for sym in sorted(_symbols):
                    await _send({"type": "subscribe", "symbol": sym})
                log.info("finnhub stream connected (%d symbols)", len(_symbols))
                backoff = 5
                # seed prev_close/change% via REST so streamed ticks carry a daily change immediately
                asyncio.create_task(service.fetch_quotes(sorted(_symbols)))
                batch: dict[str, Quote] = {}
                last_flush = asyncio.get_event_loop().time()
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if msg.get("type") != "trade":
                        continue
                    for tr in msg.get("data", []):
                        sym = tr.get("s", "").upper()
                        prev = service._quotes.get(sym)
                        pc = prev.prev_close if prev else None
                        last = float(tr["p"])
                        batch[sym] = Quote(
                            ticker=sym,
                            ts=datetime.fromtimestamp(tr["t"] / 1000, tz=UTC),
                            last=last,
                            prev_close=pc,
                            change_pct=((last / pc - 1) * 100) if pc else None,
                            volume=tr.get("v"),
                            source="finnhub-ws",
                        )
                    now = asyncio.get_event_loop().time()
                    if batch and now - last_flush >= 1.0:
                        await service.store_quotes(list(batch.values()))
                        batch.clear()
                        last_flush = now
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("finnhub stream error: %s (retry in %ss)", str(e)[:120], backoff)
        finally:
            _ws = None
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 120)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_run(), name="finnhub-stream")


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
    _task = None


def status() -> dict:
    return {
        "running": bool(_task and not _task.done()),
        "connected": _ws is not None,
        "symbols": sorted(_symbols),
    }
