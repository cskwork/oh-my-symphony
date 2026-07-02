"""Shared retry policy for external tracker HTTP calls."""

from __future__ import annotations

import random
import time
from collections.abc import Callable

import httpx


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER_SECONDS = 30.0
BASE_BACKOFF_SECONDS = 0.5
JITTER_SECONDS = 0.25


def sleep_with_jitter(delay: float) -> None:
    time.sleep(delay + random.uniform(0, JITTER_SECONDS))


def _retry_after_delay(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        delay = float(raw)
    except ValueError:
        return None
    if delay < 0:
        return None
    return min(delay, MAX_RETRY_AFTER_SECONDS)


def _backoff_delay(attempt: int, response: httpx.Response | None = None) -> float:
    retry_after = _retry_after_delay(response) if response is not None else None
    if retry_after is not None:
        return retry_after
    return BASE_BACKOFF_SECONDS * (2**attempt)


def send_with_retry(
    send: Callable[[], httpx.Response],
    *,
    max_attempts: int = 3,
    sleep: Callable[[float], None] | None = None,
) -> httpx.Response:
    """Retry transient tracker transport/status failures without new error types."""

    # Tracker mutations set idempotent state; retryable responses mean not
    # accepted yet, not a semantic validation failure.
    sleep_func = sleep or sleep_with_jitter
    last_response: httpx.Response | None = None
    last_transport_error: httpx.TransportError | None = None

    for attempt in range(max_attempts):
        try:
            response = send()
        except httpx.TransportError as exc:
            last_transport_error = exc
            if attempt == max_attempts - 1:
                raise
            sleep_func(_backoff_delay(attempt))
            continue

        last_response = response
        if response.status_code not in RETRYABLE_STATUS_CODES:
            return response
        if attempt == max_attempts - 1:
            return response
        sleep_func(_backoff_delay(attempt, response))

    if last_transport_error is not None:
        raise last_transport_error
    assert last_response is not None
    return last_response
