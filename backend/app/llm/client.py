"""Thin Claude client used by every module: structured outputs (Pydantic), prompt caching, PDF/document input,
usage + cost logging and the monthly budget gate. Model ids: claude-sonnet-5 (extraction/summaries),
claude-haiku-4-5 (bulk triage), claude-opus-5 (deep, rare).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.core import budget
from app.core.config import get_settings

log = logging.getLogger(__name__)

SONNET = "claude-sonnet-5"
HAIKU = "claude-haiku-4-5"
OPUS = "claude-opus-5"


class LLMNotConfigured(Exception):
    pass


def _client():
    s = get_settings()
    if not s.anthropic_api_key:
        raise LLMNotConfigured("TERMINAL_ANTHROPIC_API_KEY not set (SETTINGS → Anthropic API key)")
    import anthropic

    return anthropic.AsyncAnthropic(api_key=s.anthropic_api_key, max_retries=3, timeout=300.0)


def content_hash(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, default=str).encode())
    return h.hexdigest()


def pdf_block(pdf_bytes: bytes, title: str | None = None, citations: bool = False) -> dict:
    blk: dict = {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(pdf_bytes).decode(),
        },
    }
    if title:
        blk["title"] = title
    if citations:
        blk["citations"] = {"enabled": True}
    return blk


def file_block(file_id: str, title: str | None = None, citations: bool = False) -> dict:
    blk: dict = {"type": "document", "source": {"type": "file", "file_id": file_id}}
    if title:
        blk["title"] = title
    if citations:
        blk["citations"] = {"enabled": True}
    return blk


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


async def upload_file(path: Path, mime: str = "application/pdf") -> str:
    client = _client()
    data = await asyncio.to_thread(path.read_bytes)
    up = await client.files.upload(file=(path.name, data, mime))
    return up.id


async def parse[T: BaseModel](
    schema: type[T],
    system: str,
    content: str | list[dict],
    *,
    model: str = SONNET,
    purpose: str = "generic",
    max_tokens: int = 16000,
    estimated_usd: float = 0.05,
    meta: dict | None = None,
) -> tuple[T, dict]:
    """Structured output validated against `schema`. The system prompt is cached (put stable text there)."""
    await budget.check(estimated_usd)
    client = _client()
    messages = [{"role": "user", "content": content if isinstance(content, list) else [text_block(content)]}]
    resp = await client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        output_format=schema,
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"LLM refused ({getattr(resp.stop_details, 'category', None)})")
    usage = _usage(resp)
    cost = await budget.record(
        model,
        purpose,
        usage["input_tokens"],
        usage["output_tokens"],
        usage["cache_read"],
        usage["cache_write"],
        {**(meta or {}), "stop": resp.stop_reason},
    )
    usage["cost_usd"] = cost
    parsed = resp.parsed_output
    if parsed is None:
        raise RuntimeError(f"LLM returned no parsed output (stop_reason={resp.stop_reason})")
    return parsed, usage


async def complete(
    system: str,
    content: str | list[dict],
    *,
    model: str = SONNET,
    purpose: str = "generic",
    max_tokens: int = 4000,
    estimated_usd: float = 0.02,
    meta: dict | None = None,
) -> tuple[str, dict]:
    await budget.check(estimated_usd)
    client = _client()
    messages = [{"role": "user", "content": content if isinstance(content, list) else [text_block(content)]}]
    resp = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("LLM refused")
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    usage = _usage(resp)
    usage["cost_usd"] = await budget.record(
        model,
        purpose,
        usage["input_tokens"],
        usage["output_tokens"],
        usage["cache_read"],
        usage["cache_write"],
        meta or {},
    )
    return text, usage


def _usage(resp) -> dict:
    u = resp.usage
    return {
        "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
        "cache_read": int(getattr(u, "cache_read_input_tokens", 0) or 0),
        "cache_write": int(getattr(u, "cache_creation_input_tokens", 0) or 0),
    }


def configured() -> bool:
    return bool(get_settings().anthropic_api_key)
