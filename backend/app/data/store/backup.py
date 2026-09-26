"""Nightly backup: SQLite online backup + rsync-style copy of parquet/derived/pdfs into data/backups/<date>/."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime

from app.core.config import get_settings

KEEP = 7


def run_backup() -> dict:
    s = get_settings()
    stamp = datetime.now().strftime("%Y-%m-%d")
    dest = s.backup_dir / stamp
    dest.mkdir(parents=True, exist_ok=True)
    out = {"dest": str(dest)}
    if s.sqlite_path.exists():
        src = sqlite3.connect(str(s.sqlite_path))
        dst = sqlite3.connect(str(dest / "terminal.sqlite"))
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        out["sqlite"] = True
    for name in ("parquet", "derived", "pdfs"):
        d = s.data_dir / name
        if d.exists():
            shutil.copytree(d, dest / name, dirs_exist_ok=True)
            out[name] = True
    # retention
    dirs = sorted([p for p in s.backup_dir.iterdir() if p.is_dir()])
    for old in dirs[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    out["kept"] = min(len(dirs), KEEP)
    return out


def list_backups() -> list[dict]:
    s = get_settings()
    if not s.backup_dir.exists():
        return []
    out = []
    for p in sorted(s.backup_dir.iterdir(), reverse=True):
        if p.is_dir():
            files = [f for f in p.rglob("*") if f.is_file()]
            size = sum(f.stat().st_size for f in files)
            # when the backup was last written (naive UTC, like every other timestamp the API returns)
            mtime = max((f.stat().st_mtime for f in files), default=p.stat().st_mtime)
            created = datetime.fromtimestamp(mtime, UTC).replace(tzinfo=None).isoformat()
            out.append(
                {"name": p.name, "size_mb": round(size / 1e6, 1), "created_at": created, "files": len(files)}
            )
    return out
