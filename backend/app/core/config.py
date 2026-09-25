"""Application settings. Values come from environment / .env (prefix TERMINAL_) or macOS Keychain."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TERMINAL_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "terminal"
    data_dir: Path = PROJECT_ROOT / "data"
    timezone: str = "Asia/Jerusalem"
    host: str = "127.0.0.1"
    port: int = 8710
    log_level: str = "INFO"

    # Providers
    sec_user_agent: str = ""  # "Name email" — mandatory for sec.gov
    fred_api_key: str = ""
    finnhub_api_key: str = ""
    fmp_api_key: str = ""
    alpaca_key_id: str = ""
    alpaca_secret: str = ""
    anthropic_api_key: str = ""
    massive_api_key: str = ""
    alphavantage_api_key: str = ""

    # Alerts
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_ops_chat_id: str = ""

    # Policy
    grey_sources_enabled: bool = True
    llm_monthly_budget_usd: float = 20.0
    scheduler_enabled: bool = True

    # Derived paths
    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "terminal.sqlite"

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "parquet"

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdfs"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def sqlite_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.sqlite_path}"

    @property
    def sqlite_sync_url(self) -> str:
        return f"sqlite:///{self.sqlite_path}"

    def ensure_dirs(self) -> None:
        for p in (
            self.data_dir,
            self.parquet_dir,
            self.derived_dir,
            self.cache_dir,
            self.pdf_dir,
            self.backup_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)

    secrets_overridden: set[str] = Field(default_factory=set, exclude=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # Fill empty secrets from the OS keychain when available.
    from app.core.secrets import fill_from_keyring

    fill_from_keyring(s)
    return s
