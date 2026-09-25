"""SQLite FTS5 for notes (trigram tokenizer handles Hebrew prefixes/suffixes; final letters normalized)."""

from __future__ import annotations

from sqlalchemy import text

from app.data.store.sqlite import get_engine

FINALS = str.maketrans({"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"})


def normalize(s: str) -> str:
    return (s or "").translate(FINALS).lower()


async def ensure_fts() -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(note_id UNINDEXED, title, body, tickers, tags, tokenize='trigram')"
            )
        )


async def index_note(note_id: int, title: str, body: str, tickers: list[str], tags: list[str]) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(text("DELETE FROM notes_fts WHERE note_id = :id"), {"id": note_id})
        await conn.execute(
            text("INSERT INTO notes_fts(note_id, title, body, tickers, tags) VALUES (:id, :t, :b, :k, :g)"),
            {
                "id": note_id,
                "t": normalize(title),
                "b": normalize(body),
                "k": " ".join(tickers).lower(),
                "g": " ".join(tags).lower(),
            },
        )


async def remove_note(note_id: int) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(text("DELETE FROM notes_fts WHERE note_id = :id"), {"id": note_id})


async def search_notes(q: str, limit: int = 50) -> list[int]:
    q = normalize(q).strip()
    if len(q) < 3:
        return []
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT note_id FROM notes_fts WHERE notes_fts MATCH :q ORDER BY rank LIMIT :n"),
                {"q": f'"{q}"', "n": limit},
            )
        ).all()
    return [int(r[0]) for r in rows]
