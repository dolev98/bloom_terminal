"""Secret storage: environment first, then macOS Keychain (via `keyring`). Settings UI writes to keyring."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

SERVICE = "maor-terminal"
SECRET_FIELDS = (
    "sec_user_agent",
    "fred_api_key",
    "finnhub_api_key",
    "fmp_api_key",
    "alpaca_key_id",
    "alpaca_secret",
    "anthropic_api_key",
    "massive_api_key",
    "alphavantage_api_key",
    "telegram_bot_token",
    "telegram_chat_id",
    "telegram_ops_chat_id",
)


def _keyring():
    try:
        import keyring

        return keyring
    except Exception:  # pragma: no cover - keyring backend missing
        return None


def get_secret(name: str) -> str | None:
    kr = _keyring()
    if kr is None:
        return None
    try:
        return kr.get_password(SERVICE, name)
    except Exception as e:  # pragma: no cover
        log.debug("keyring read failed for %s: %s", name, e)
        return None


def set_secret(name: str, value: str) -> bool:
    kr = _keyring()
    if kr is None:
        return False
    try:
        if value:
            kr.set_password(SERVICE, name, value)
        else:
            try:
                kr.delete_password(SERVICE, name)
            except Exception:
                pass
        return True
    except Exception as e:  # pragma: no cover
        log.warning("keyring write failed for %s: %s", name, e)
        return False


def fill_from_keyring(settings) -> None:
    for field in SECRET_FIELDS:
        if getattr(settings, field, ""):
            continue
        val = get_secret(field)
        if val:
            setattr(settings, field, val)
            settings.secrets_overridden.add(field)


def mask(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "•" * len(value)
    return value[:3] + "•" * (len(value) - 6) + value[-3:]
