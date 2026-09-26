"""Per-provider async token buckets (second / minute / day windows) + concurrency cap. Feeds the SYS quota meters."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

from app.data.providers.base import RateSpec


@dataclass
class _Window:
    limit: float
    seconds: float
    events: deque = field(default_factory=deque)

    def prune(self, now: float) -> None:
        cutoff = now - self.seconds
        while self.events and self.events[0] <= cutoff:
            self.events.popleft()

    def wait_time(self, now: float) -> float:
        self.prune(now)
        if len(self.events) < self.limit:
            return 0.0
        return max(0.0, self.events[0] + self.seconds - now)

    def record(self, now: float) -> None:
        self.events.append(now)


class RateLimiter:
    def __init__(self, provider_id: str, spec: RateSpec):
        self.provider_id = provider_id
        self.spec = spec
        self.windows: list[_Window] = []
        if spec.per_second:
            self.windows.append(_Window(spec.per_second, 1.0))
        if spec.per_minute:
            self.windows.append(_Window(spec.per_minute, 60.0))
        if spec.per_day:
            self.windows.append(_Window(spec.per_day, 86400.0))
        self._sem = asyncio.Semaphore(max(1, spec.concurrency))
        self._lock = asyncio.Lock()
        self.total_calls = 0
        self.total_wait = 0.0

    async def acquire(self) -> None:
        await self._sem.acquire()
        try:
            while True:
                async with self._lock:
                    now = time.monotonic()
                    wait = max((w.wait_time(now) for w in self.windows), default=0.0)
                    if wait <= 0:
                        for w in self.windows:
                            w.record(now)
                        self.total_calls += 1
                        return
                self.total_wait += wait
                await asyncio.sleep(wait)
        except BaseException:
            self._sem.release()
            raise

    def release(self) -> None:
        self._sem.release()

    def snapshot(self) -> dict:
        now = time.monotonic()
        used = {}
        for w in self.windows:
            w.prune(now)
            label = {1.0: "per_second", 60.0: "per_minute", 86400.0: "per_day"}[w.seconds]
            used[label] = {"used": len(w.events), "limit": w.limit}
        return {
            "provider": self.provider_id,
            "windows": used,
            "total_calls": self.total_calls,
            "total_wait_s": round(self.total_wait, 1),
        }


class LimiterPool:
    def __init__(self):
        self._limiters: dict[str, RateLimiter] = {}

    def get(self, provider_id: str, spec: RateSpec) -> RateLimiter:
        lim = self._limiters.get(provider_id)
        if lim is None:
            lim = RateLimiter(provider_id, spec)
            self._limiters[provider_id] = lim
        return lim

    def snapshot(self) -> list[dict]:
        return [lim.snapshot() for lim in self._limiters.values()]
