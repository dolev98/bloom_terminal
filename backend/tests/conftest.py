import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="terminal-test-"))
os.environ.update(
    {
        "TERMINAL_DATA_DIR": str(_TMP),
        "TERMINAL_SCHEDULER_ENABLED": "false",
        "TERMINAL_FRED_API_KEY": "test-key",
        "TERMINAL_FINNHUB_API_KEY": "test-key",
        "TERMINAL_SEC_USER_AGENT": "Test Runner test@example.com",
        "TERMINAL_TELEGRAM_BOT_TOKEN": "",
        "TERMINAL_LOG_LEVEL": "WARNING",
    }
)

from app.core.config import get_settings  # noqa: E402
from app.data import series_service  # noqa: E402
from app.data.registry import build_registry, set_registry  # noqa: E402
from app.data.store.parquet import ParquetStore  # noqa: E402
from app.data.store.sqlite import create_all, dispose, init_engine  # noqa: E402


@pytest.fixture
def settings():
    get_settings.cache_clear()
    s = get_settings()
    s.ensure_dirs()
    return s


@pytest.fixture
async def db(settings, tmp_path):
    """Fresh SQLite + parquet store per test (async tests)."""
    db_path = tmp_path / "t.sqlite"
    init_engine(f"sqlite+aiosqlite:///{db_path}")
    await create_all()
    series_service.set_store(ParquetStore(tmp_path / "parquet"))
    set_registry(build_registry(settings))
    yield
    await dispose()
    series_service.set_store(None)
    set_registry(None)


@pytest.fixture
def client(settings, tmp_path, monkeypatch):
    """Sync TestClient that runs the real lifespan (seed catalog, registry) against a temp data dir."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.ensure_dirs()
    series_service.set_store(None)
    set_registry(None)
    from app.main import app

    with TestClient(app) as c:
        yield c
    series_service.set_store(None)
    set_registry(None)
