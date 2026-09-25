"""Dev entry: `uv run backend/run.py` (reload) — or `uv run uvicorn app.main:app --app-dir backend`."""

import uvicorn

from app.core.config import get_settings

if __name__ == "__main__":
    s = get_settings()
    uvicorn.run("app.main:app", host=s.host, port=s.port, reload=True, app_dir="backend")
