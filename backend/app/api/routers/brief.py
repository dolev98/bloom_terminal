from __future__ import annotations

from fastapi import APIRouter

from app.brief import service

router = APIRouter(prefix="/api/brief", tags=["brief"])


@router.get("")
async def brief() -> dict:
    return await service.build_brief()


@router.get("/preview")
async def preview() -> dict:
    b = await service.build_brief()
    return {"text": service.render_telegram(b)}


@router.post("/send")
async def send(force: bool = True) -> dict:
    return await service.send_brief(force=force)
