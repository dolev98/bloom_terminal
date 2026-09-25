from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.data.registry import get_registry

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health() -> dict:
    s = get_settings()
    return {
        "ok": True,
        "app": s.app_name,
        "timezone": s.timezone,
        "grey_sources_enabled": get_registry().grey_enabled,
    }


@router.get("/api/health/providers")
async def providers_health() -> list[dict]:
    reg = get_registry()
    out = []
    for p in reg.all():
        usable, why = reg.is_usable(p)
        row = {"id": p.id, "name": p.name, "usable": usable, "reason": why, "requires": list(p.requires)}
        if usable:
            try:
                row.update(await p.health())
            except Exception as e:
                row.update({"ok": False, "error": str(e)[:200]})
        out.append(row)
    return out
