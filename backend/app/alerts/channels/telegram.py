"""Telegram Bot API channel (free). Market alerts and ops alerts go to separate chats."""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.data.http import get_client

log = logging.getLogger(__name__)
MAX_LEN = 4096


async def send_telegram(text: str, ops: bool = False, parse_mode: str | None = "HTML") -> bool:
    s = get_settings()
    chat = s.telegram_ops_chat_id or s.telegram_chat_id if ops else s.telegram_chat_id
    if not s.telegram_bot_token or not chat:
        log.info("telegram not configured; message dropped: %s", text[:80])
        return False
    client = get_client()
    ok = True
    for chunk in _chunks(text):
        payload = {"chat_id": chat, "text": chunk, "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        resp = await client.post(
            f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage", json=payload
        )
        if resp.status_code != 200:
            log.warning("telegram send failed %s: %s", resp.status_code, resp.text[:200])
            ok = False
    return ok


async def get_updates_chat_ids() -> list[dict]:
    """Helper for onboarding: read recent /start messages to discover chat ids."""
    s = get_settings()
    if not s.telegram_bot_token:
        return []
    client = get_client()
    resp = await client.get(f"https://api.telegram.org/bot{s.telegram_bot_token}/getUpdates")
    if resp.status_code != 200:
        return []
    out = []
    for u in resp.json().get("result", []):
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            out.append(
                {
                    "chat_id": chat["id"],
                    "title": chat.get("title") or chat.get("username") or chat.get("first_name"),
                    "text": msg.get("text"),
                }
            )
    return out


def _chunks(text: str):
    while text:
        yield text[:MAX_LEN]
        text = text[MAX_LEN:]
