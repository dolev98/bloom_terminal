"""Provider registry: instantiates configured providers, wraps calls with limiter/retry/fetch-log, enforces the grey-source gate."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import Settings
from app.data.providers.base import Capability, Provider, ProviderError, SeriesSpec
from app.data.ratelimit import LimiterPool
from app.data.store.models import FetchLog
from app.data.store.sqlite import session_scope

log = logging.getLogger(__name__)


class GreySourceDisabled(ProviderError):
    pass


class ProviderRegistry:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._providers: dict[str, Provider] = {}
        self.limiters = LimiterPool()
        self.grey_enabled: bool = settings.grey_sources_enabled

    # --- registration ----------------------------------------------------
    def register(self, provider: Provider) -> None:
        self._providers[provider.id] = provider

    def get(self, provider_id: str) -> Provider:
        p = self._providers.get(provider_id)
        if p is None:
            raise ProviderError(f"unknown provider {provider_id!r}")
        return p

    def all(self) -> list[Provider]:
        return list(self._providers.values())

    def for_capability(self, cap: Capability) -> list[Provider]:
        return [p for p in self._providers.values() if cap in p.capabilities]

    def resolve(self, series_id: str) -> tuple[Provider, str, str | None]:
        provider_id, key, fld = SeriesSpec.parse_id(series_id)
        return self.get(provider_id), key, fld

    def is_usable(self, provider: Provider) -> tuple[bool, str]:
        if not provider.configured(self.settings):
            return False, f"missing settings: {', '.join(provider.requires)}"
        if provider.license.grey and not self.grey_enabled:
            return False, "grey sources disabled in prefs"
        return True, "ok"

    # --- wrapped calls ---------------------------------------------------
    async def call(self, provider: Provider, op: str, fn: Callable[[], Awaitable[Any]], key: str = "") -> Any:
        ok, why = self.is_usable(provider)
        if not ok:
            if "grey" in why:
                raise GreySourceDisabled(f"{provider.id}: {why}")
            raise ProviderError(f"{provider.id}: {why}")
        lim = self.limiters.get(provider.id, provider.rate)
        await lim.acquire()
        t0 = time.perf_counter()
        status, err, rows = "ok", None, 0
        try:
            result = await fn()
            try:
                rows = len(result)  # type: ignore[arg-type]
            except Exception:
                rows = 0
            return result
        except Exception as e:
            status, err = "error", f"{type(e).__name__}: {e}"[:500]
            raise
        finally:
            lim.release()
            dur = int((time.perf_counter() - t0) * 1000)
            try:
                async with session_scope() as s:
                    s.add(
                        FetchLog(
                            provider=provider.id,
                            op=op,
                            key=key[:300],
                            status=status,
                            duration_ms=dur,
                            rows=rows,
                            error=err,
                        )
                    )
            except Exception as e:  # pragma: no cover
                log.debug("fetch_log write failed: %s", e)

    def status(self) -> list[dict]:
        out = []
        for p in self._providers.values():
            ok, why = self.is_usable(p)
            out.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "capabilities": [c.name for c in Capability if c and c in p.capabilities],
                    "usable": ok,
                    "reason": why,
                    "grey": p.license.grey,
                    "egress": p.egress,
                    "attribution": p.license.attribution,
                    "requires": list(p.requires),
                }
            )
        return out


def build_registry(settings: Settings) -> ProviderRegistry:
    from app.data.providers.boi import BoiProvider
    from app.data.providers.edgar import EdgarProvider
    from app.data.providers.finnhub import FinnhubProvider
    from app.data.providers.fred import FredProvider
    from app.data.providers.hebcal import HebcalProvider
    from app.data.providers.manual import ManualProvider
    from app.data.providers.yfinance_provider import YFinanceProvider

    reg = ProviderRegistry(settings)
    reg.register(FredProvider(api_key=settings.fred_api_key))
    reg.register(BoiProvider())
    reg.register(EdgarProvider())
    reg.register(HebcalProvider())
    reg.register(FinnhubProvider(api_key=settings.finnhub_api_key))
    reg.register(ManualProvider())
    reg.register(YFinanceProvider())
    try:
        from app.macro.providers import PROVIDERS as _MACRO

        for p in _MACRO:
            reg.register(p)
    except ImportError:  # pragma: no cover
        pass
    try:
        from app.statements.xbrl_org import PROVIDERS as _STMT

        for p in _STMT:
            reg.register(p)
    except ImportError:  # pragma: no cover
        pass
    try:
        from app.news.sources import PROVIDERS as _NEWS

        for p in _NEWS:
            reg.register(p)
    except ImportError:  # pragma: no cover
        pass
    try:
        from app.valuation.providers import PROVIDERS as _VAL

        for p in _VAL:
            reg.register(p)
    except ImportError:  # pragma: no cover
        pass
    return reg


_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        from app.core.config import get_settings

        _registry = build_registry(get_settings())
    return _registry


def set_registry(reg: ProviderRegistry | None) -> None:
    global _registry
    _registry = reg
