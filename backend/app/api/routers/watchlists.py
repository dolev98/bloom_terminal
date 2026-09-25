from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.data.store.models import Watchlist, WatchlistItem
from app.data.store.sqlite import session_scope
from app.market import service, stream

router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])
DEFAULT_TICKERS = [
    "SPY",
    "QQQ",
    "IWM",
    "AAPL",
    "MSFT",
    "NVDA",
    "GOOGL",
    "AMZN",
    "TEVA",
    "NICE",
    "CHKP",
    "ESLT",
]


def _dump(w: Watchlist) -> dict:
    return {
        "id": w.id,
        "name": w.name,
        "items": [{"id": i.id, "ticker": i.ticker, "position": i.position, "note": i.note} for i in w.items],
    }


async def ensure_default() -> None:
    async with session_scope() as s:
        exists = (await s.execute(select(Watchlist.id).limit(1))).first()
        if exists:
            return
        w = Watchlist(name="Main")
        s.add(w)
        await s.flush()
        for i, t in enumerate(DEFAULT_TICKERS):
            s.add(WatchlistItem(watchlist_id=w.id, ticker=t, position=i))


async def all_tickers() -> list[str]:
    async with session_scope() as s:
        rows = (await s.execute(select(WatchlistItem.ticker))).scalars().all()
    return sorted({r.upper() for r in rows})


async def sync_stream() -> None:
    await stream.set_symbols(await all_tickers())


@router.get("")
async def list_watchlists() -> list[dict]:
    async with session_scope() as s:
        rows = (
            (await s.execute(select(Watchlist).options(selectinload(Watchlist.items)).order_by(Watchlist.id)))
            .scalars()
            .all()
        )
        return [_dump(w) for w in rows]


class WatchlistCreate(BaseModel):
    name: str


@router.post("")
async def create(req: WatchlistCreate) -> dict:
    async with session_scope() as s:
        w = Watchlist(name=req.name.strip())
        s.add(w)
        await s.flush()
        return {"id": w.id, "name": w.name, "items": []}


@router.delete("/{wid}")
async def delete(wid: int) -> dict:
    async with session_scope() as s:
        w = await s.get(Watchlist, wid)
        if not w:
            raise HTTPException(404)
        await s.delete(w)
    await sync_stream()
    return {"deleted": True}


class ItemAdd(BaseModel):
    ticker: str
    note: str | None = None


@router.post("/{wid}/items")
async def add_item(wid: int, req: ItemAdd) -> dict:
    t = req.ticker.strip().upper()
    async with session_scope() as s:
        w = (
            await s.execute(
                select(Watchlist).options(selectinload(Watchlist.items)).where(Watchlist.id == wid)
            )
        ).scalar_one_or_none()
        if not w:
            raise HTTPException(404)
        if any(i.ticker == t for i in w.items):
            raise HTTPException(409, "already in list")
        s.add(WatchlistItem(watchlist_id=wid, ticker=t, position=len(w.items), note=req.note))
    await sync_stream()
    await service.fetch_quotes([t])
    return {"added": t}


@router.delete("/{wid}/items/{ticker}")
async def remove_item(wid: int, ticker: str) -> dict:
    async with session_scope() as s:
        row = (
            await s.execute(
                select(WatchlistItem).where(
                    WatchlistItem.watchlist_id == wid, WatchlistItem.ticker == ticker.upper()
                )
            )
        ).scalar_one_or_none()
        if not row:
            raise HTTPException(404)
        await s.delete(row)
    await sync_stream()
    return {"removed": ticker.upper()}


@router.post("/{wid}/refresh")
async def refresh_list(wid: int) -> dict:
    async with session_scope() as s:
        w = (
            await s.execute(
                select(Watchlist).options(selectinload(Watchlist.items)).where(Watchlist.id == wid)
            )
        ).scalar_one_or_none()
        if not w:
            raise HTTPException(404)
        tickers = [i.ticker for i in w.items]
    qs = await service.fetch_quotes(tickers)
    return {"quotes": len(qs)}
