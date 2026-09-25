from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select

from app.data.store.models import Note
from app.data.store.sqlite import session_scope
from app.market import service
from app.search import fts

router = APIRouter(prefix="/api/notes", tags=["notes"])


def _dump(n: Note) -> dict:
    return {
        "id": n.id,
        "title": n.title,
        "body": n.body,
        "tickers": n.tickers,
        "tags": n.tags,
        "price_at_note": n.price_at_note,
        "created_at": n.created_at,
        "updated_at": n.updated_at,
    }


class NoteIn(BaseModel):
    title: str = ""
    body: str = ""
    tickers: list[str] = []
    tags: list[str] = []


@router.get("")
async def list_notes(q: str | None = None, ticker: str | None = None, limit: int = 100) -> list[dict]:
    ids: list[int] | None = None
    if q:
        ids = await fts.search_notes(q, limit)
        if not ids:
            return []
    async with session_scope() as s:
        stmt = select(Note).order_by(desc(Note.updated_at)).limit(limit)
        if ids is not None:
            stmt = select(Note).where(Note.id.in_(ids))
        rows = (await s.execute(stmt)).scalars().all()
    out = [_dump(n) for n in rows]
    if ticker:
        out = [n for n in out if ticker.upper() in [t.upper() for t in n["tickers"]]]
    if ids is not None:
        order = {i: k for k, i in enumerate(ids)}
        out.sort(key=lambda n: order.get(n["id"], 1e9))
    return out


@router.post("")
async def create(req: NoteIn) -> dict:
    tickers = [t.strip().upper() for t in req.tickers if t.strip()]
    prices = {q["ticker"]: q["last"] for q in service.cached_quotes(tickers)} if tickers else {}
    async with session_scope() as s:
        n = Note(title=req.title, body=req.body, tickers=tickers, tags=req.tags, price_at_note=prices)
        s.add(n)
        await s.flush()
        nid = n.id
    await fts.index_note(nid, req.title, req.body, tickers, req.tags)
    async with session_scope() as s:
        return _dump(await s.get(Note, nid))


@router.put("/{nid}")
async def update(nid: int, req: NoteIn) -> dict:
    async with session_scope() as s:
        n = await s.get(Note, nid)
        if not n:
            raise HTTPException(404)
        n.title, n.body, n.tickers, n.tags = req.title, req.body, [t.upper() for t in req.tickers], req.tags
    await fts.index_note(nid, req.title, req.body, req.tickers, req.tags)
    async with session_scope() as s:
        return _dump(await s.get(Note, nid))


@router.delete("/{nid}")
async def delete(nid: int) -> dict:
    async with session_scope() as s:
        n = await s.get(Note, nid)
        if not n:
            raise HTTPException(404)
        await s.delete(n)
    await fts.remove_note(nid)
    return {"deleted": True}
