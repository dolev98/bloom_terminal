from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.market import service
from app.ws.hub import get_hub

router = APIRouter(tags=["ws"])


@router.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    hub = get_hub()
    await hub.connect(ws)
    try:
        await ws.send_text(
            json.dumps({"type": "hello", "data": {"quotes": service.cached_quotes()}}, default=str)
        )
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=30)
                if msg == "ping":
                    await ws.send_text('{"type":"pong"}')
            except TimeoutError:
                await ws.send_text('{"type":"ping"}')
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(ws)
