"""Retry policy shared by all providers: exponential backoff with jitter on 429/5xx/timeouts, honoring Retry-After."""

from __future__ import annotations

import logging

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

log = logging.getLogger(__name__)

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class RetryableHTTPError(Exception):
    def __init__(self, response: httpx.Response):
        self.response = response
        self.retry_after = _retry_after_seconds(response)
        super().__init__(f"HTTP {response.status_code} from {response.request.url}")


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    ra = resp.headers.get("Retry-After")
    if not ra:
        return None
    try:
        return float(ra)
    except ValueError:
        return None


def raise_for_retry(resp: httpx.Response) -> httpx.Response:
    if resp.status_code in RETRY_STATUS:
        raise RetryableHTTPError(resp)
    resp.raise_for_status()
    return resp


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, RetryableHTTPError | httpx.TimeoutException | httpx.TransportError)


class _Wait:
    def __init__(self):
        self._base = wait_exponential_jitter(initial=1, max=60, jitter=2)

    def __call__(self, state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome else None
        if isinstance(exc, RetryableHTTPError) and exc.retry_after:
            return min(exc.retry_after, 300.0)
        return self._base(state)


def retrying(attempts: int = 5) -> AsyncRetrying:
    return AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=_Wait(),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
        before_sleep=lambda s: log.warning(
            "retry %s/%s after %s", s.attempt_number, attempts, s.outcome.exception() if s.outcome else "?"
        ),
    )
