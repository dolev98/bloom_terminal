from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.data.store.models import Layout
from app.data.store.sqlite import session_scope

router = APIRouter(prefix="/api/layouts", tags=["layouts"])


@router.get("")
async def list_layouts() -> list[dict]:
    async with session_scope() as s:
        rows = (await s.execute(select(Layout).order_by(Layout.name))).scalars().all()
    return [
        {"id": r.id, "name": r.name, "is_default": r.is_default, "updated_at": r.updated_at} for r in rows
    ]


@router.get("/{name}")
async def get_layout(name: str) -> dict:
    async with session_scope() as s:
        row = (await s.execute(select(Layout).where(Layout.name == name))).scalar_one_or_none()
    if not row:
        raise HTTPException(404)
    return {"name": row.name, "payload": row.payload, "is_default": row.is_default}


class LayoutIn(BaseModel):
    payload: dict
    is_default: bool = False


@router.put("/{name}")
async def save_layout(name: str, req: LayoutIn) -> dict:
    async with session_scope() as s:
        row = (await s.execute(select(Layout).where(Layout.name == name))).scalar_one_or_none()
        if req.is_default:
            for r in (await s.execute(select(Layout).where(Layout.is_default.is_(True)))).scalars():
                r.is_default = False
        if row is None:
            s.add(Layout(name=name, payload=req.payload, is_default=req.is_default))
        else:
            row.payload = req.payload
            row.is_default = req.is_default
    return {"saved": name}


@router.delete("/{name}")
async def delete_layout(name: str) -> dict:
    async with session_scope() as s:
        row = (await s.execute(select(Layout).where(Layout.name == name))).scalar_one_or_none()
        if not row:
            raise HTTPException(404)
        await s.delete(row)
    return {"deleted": name}
