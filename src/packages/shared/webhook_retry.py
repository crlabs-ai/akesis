"""Scheduling for transient webhook verification failures.

Policy (approved):
- Provider-directed delays (``Retry-After`` or ``x-ratelimit-reset``) are honoured in full. A
  provider delay is never shortened to satisfy the local backoff cap, so a retry is never
  scheduled earlier than the provider asked for.
- Non-finite provider values are invalid and fall back to computed backoff.
- A finite provider delay above the sanity ceiling is not treated as absent. It is scheduled
  for its full duration as a ``provider_hold``: an operator-visible, non-fallback state that the
  worker releases when due. It never silently becomes a shorter retry.
- Computed backoff is exponential from ``base_delay`` with jitter, capped at ``max_delay``.
"""

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from random import random

RETRYABLE_ERROR = "retryable_error"
PROVIDER_HOLD = "provider_hold"


@dataclass(frozen=True)
class RetrySchedule:
    status: str
    next_retry_at: datetime


def _provider_delay(headers: Mapping[str, str], now: datetime) -> float | None:
    """Return a finite provider-directed delay in seconds (may exceed the ceiling), or None."""
    delay: float | None = None
    retry_after = headers.get("retry-after")
    if retry_after:
        try:
            if retry_after.isascii() and retry_after.isdigit():
                delay = float(retry_after)
            else:
                value = parsedate_to_datetime(retry_after)
                if value.tzinfo is not None:
                    delay = (value.astimezone(UTC) - now).total_seconds()
        except (ValueError, OverflowError, TypeError):
            delay = None
    if delay is None and headers.get("x-ratelimit-reset"):
        try:
            delay = float(headers["x-ratelimit-reset"]) - now.timestamp()
        except (ValueError, OverflowError):
            delay = None
    if delay is None or not math.isfinite(delay):
        return None
    return max(0.0, delay)


def classify_retry(
    headers: Mapping[str, str],
    attempt: int,
    *,
    now: datetime,
    base_delay: int,
    max_delay: int,
    provider_ceiling: int,
    random_value: Callable[[], float] = random,
) -> RetrySchedule:
    """Decide the state and due time for the next attempt. Never blocks the request handler."""
    provider = _provider_delay(headers, now)
    if provider is not None and provider > provider_ceiling:
        return RetrySchedule(PROVIDER_HOLD, now + timedelta(seconds=provider))
    if provider is not None:
        return RetrySchedule(RETRYABLE_ERROR, now + timedelta(seconds=provider))
    return RetrySchedule(
        RETRYABLE_ERROR,
        now + timedelta(seconds=_backoff_seconds(attempt, base_delay, max_delay, random_value)),
    )


def next_retry_time(
    headers: Mapping[str, str],
    attempt: int,
    *,
    now: datetime,
    base_delay: int,
    max_delay: int,
    provider_ceiling: int,
    random_value: Callable[[], float] = random,
) -> datetime:
    """Due time for the next attempt; see ``classify_retry`` for the state it belongs to."""
    return classify_retry(
        headers,
        attempt,
        now=now,
        base_delay=base_delay,
        max_delay=max_delay,
        provider_ceiling=provider_ceiling,
        random_value=random_value,
    ).next_retry_at


def _backoff_seconds(
    attempt: int, base_delay: int, max_delay: int, random_value: Callable[[], float]
) -> float:
    exponent = min(max(attempt - 1, 0), 20)
    ceiling: float = float(min(max_delay, base_delay * (2**exponent)))
    jitter: float = 0.5 + 0.5 * random_value()
    return max(0.0, ceiling * jitter)
