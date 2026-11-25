"""Unit coverage for webhook retry scheduling under the approved policy.

Policy under test:
- Provider-directed delays are honoured in full and never shortened by the local backoff cap.
- Non-finite provider values, and provider delays above the sanity ceiling, are invalid and
  fall back to computed backoff.
- Computed backoff is 60s base, doubling per attempt, jittered to 50-100% of the ceiling, and
  capped at 900s.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from src.packages.shared.webhook_retry import RetrySchedule, classify_retry, next_retry_time

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
BASE = 60
MAX = 900
CEILING = 86400


def _no_jitter() -> float:
    return 1.0  # Full ceiling; removes randomness from backoff assertions.


def _classify(
    headers: dict[str, str],
    attempt: int,
    *,
    random_value: Callable[[], float] = _no_jitter,
) -> RetrySchedule:
    return classify_retry(
        headers,
        attempt,
        now=NOW,
        base_delay=BASE,
        max_delay=MAX,
        provider_ceiling=CEILING,
        random_value=random_value,
    )


def _schedule(
    headers: dict[str, str],
    attempt: int,
    *,
    random_value: Callable[[], float] = _no_jitter,
) -> datetime:
    return next_retry_time(
        headers,
        attempt,
        now=NOW,
        base_delay=BASE,
        max_delay=MAX,
        provider_ceiling=CEILING,
        random_value=random_value,
    )


def test_retry_after_seconds_sets_exact_delay() -> None:
    assert _schedule({"retry-after": "120"}, 1) == NOW + timedelta(seconds=120)


def test_provider_delay_above_local_cap_is_honoured_not_shortened() -> None:
    # 1200s exceeds the 900s local maximum. The provider asked for 1200s, so the retry must not
    # run earlier than that.
    assert _schedule({"retry-after": "1200"}, 1) == NOW + timedelta(seconds=1200)


def test_provider_delay_above_sanity_ceiling_is_an_operator_visible_hold() -> None:
    # Previously this fell back to a shorter computed retry. A valid provider delay must never
    # silently become an earlier retry, so it is held until the provider's own due time.
    schedule = _classify({"retry-after": str(CEILING + 1)}, 1)
    assert schedule.status == "provider_hold"
    assert schedule.next_retry_at == NOW + timedelta(seconds=CEILING + 1)


def test_provider_delay_within_ceiling_is_a_normal_scheduled_retry() -> None:
    schedule = _classify({"retry-after": "7200"}, 1)
    assert schedule.status == "retryable_error"
    assert schedule.next_retry_at == NOW + timedelta(seconds=7200)


def test_non_finite_provider_value_falls_back_to_backoff_not_a_hold() -> None:
    schedule = _classify({"x-ratelimit-reset": "nan"}, 1, random_value=_no_jitter)
    assert schedule.status == "retryable_error"
    assert schedule.next_retry_at == NOW + timedelta(seconds=BASE)


def test_retry_after_http_date_sets_delay_until_that_time() -> None:
    target = NOW + timedelta(seconds=300)
    assert _schedule({"retry-after": format_datetime(target, usegmt=True)}, 1) == target


def test_past_retry_after_date_never_schedules_before_now() -> None:
    past = NOW - timedelta(hours=1)
    assert _schedule({"retry-after": format_datetime(past, usegmt=True)}, 1) == NOW


def test_rate_limit_reset_epoch_sets_delay() -> None:
    reset = NOW + timedelta(seconds=90)
    at = _schedule({"x-ratelimit-reset": str(int(reset.timestamp()))}, 1)
    assert at == reset


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-inf", "infinity"])
def test_non_finite_rate_limit_reset_is_rejected(value: str) -> None:
    # float("nan") previously produced an immediate retry. Non-finite values must not be used.
    assert _schedule({"x-ratelimit-reset": value}, 1) == NOW + timedelta(seconds=BASE)


def test_non_finite_values_never_yield_an_immediate_retry() -> None:
    at = _schedule({"x-ratelimit-reset": "nan"}, 2)
    assert at > NOW


def test_malformed_retry_after_falls_back_to_backoff() -> None:
    assert _schedule({"retry-after": "not-a-delay"}, 1) == NOW + timedelta(seconds=BASE)


def test_past_rate_limit_reset_retries_now_without_going_negative() -> None:
    past = NOW - timedelta(minutes=5)
    assert _schedule({"x-ratelimit-reset": str(int(past.timestamp()))}, 1) == NOW


@pytest.mark.parametrize(
    ("attempt", "expected_seconds"),
    [(1, 60), (2, 120), (3, 240), (4, 480), (5, 900)],
)
def test_backoff_doubles_per_attempt_and_is_capped(attempt: int, expected_seconds: int) -> None:
    assert _schedule({}, attempt) == NOW + timedelta(seconds=expected_seconds)


def test_backoff_stays_at_cap_for_large_attempts() -> None:
    assert _schedule({}, 50) == NOW + timedelta(seconds=MAX)


def test_jitter_keeps_backoff_within_half_to_full_ceiling() -> None:
    low = _schedule({}, 3, random_value=lambda: 0.0)
    high = _schedule({}, 3, random_value=_no_jitter)
    assert timedelta(seconds=120) <= low - NOW <= high - NOW == timedelta(seconds=240)
