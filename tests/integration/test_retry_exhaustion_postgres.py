"""Retry exhaustion, atomic reclaim and redelivery behaviour against disposable PostgreSQL.

Requires AKESIS_DISPOSABLE_TEST_DATABASE_URL pointing at a loopback akesis_test database. The
fixture refuses any other database.
"""

import asyncio
import hashlib
import hmac
import json
import os
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.apps.api.main import create_app
from src.packages.database.models import WebhookEventModel
from src.packages.database.session import get_db_session
from src.packages.database.webhook_events import (
    EXHAUSTED_FAILURE_CODE,
    DeliveryConflictError,
    WebhookEventRepository,
)
from src.packages.shared.config import Settings, settings

MAX_ATTEMPTS = 5


@pytest.fixture
async def database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("AKESIS_DISPOSABLE_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("Explicit disposable PostgreSQL test database not configured")
    parsed = urlsplit(url)
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.path != "/akesis_test":
        pytest.fail("Refusing a database other than the explicitly designated local test database")
    engine = create_async_engine(url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _unique(prefix: str) -> str:
    # GitHub delivery IDs must match [A-Za-z0-9-]; the route rejects anything else.
    return f"{prefix}-{time.time_ns()}"


async def _seed(
    database: async_sessionmaker[AsyncSession], delivery_id: str, **values: object
) -> None:
    """Insert a delivery and overwrite selected state to model a precise starting point."""
    async with database() as db:
        async with db.begin():
            await WebhookEventRepository(db).register(delivery_id, "a" * 64, "workflow_run")
    async with database() as db:
        row = await db.get(WebhookEventModel, delivery_id)
        assert row is not None
        for key, value in values.items():
            setattr(row, key, value)
        await db.commit()


async def _row(database: async_sessionmaker[AsyncSession], delivery_id: str) -> WebhookEventModel:
    async with database() as db:
        row = await db.get(WebhookEventModel, delivery_id, populate_existing=True)
    assert row is not None
    return row


def _past() -> datetime:
    return datetime.now(UTC) - timedelta(seconds=1)


# --- Repository boundary, concurrency and idempotency -----------------------------------------


@pytest.mark.asyncio
async def test_retry_claim_at_final_permitted_attempt_is_allowed(
    database: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = _unique("test-boundary-last")
    await _seed(
        database,
        delivery_id,
        status="retryable_error",
        attempt_count=MAX_ATTEMPTS - 1,
        next_retry_at=_past(),
    )
    async with database() as db:
        async with db.begin():
            assert await WebhookEventRepository(db).claim(delivery_id, MAX_ATTEMPTS)
    row = await _row(database, delivery_id)
    assert row.status == "verifying" and row.attempt_count == MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_spent_budget_refuses_claim_without_consuming_an_attempt(
    database: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = _unique("test-spent")
    await _seed(
        database,
        delivery_id,
        status="retryable_error",
        attempt_count=MAX_ATTEMPTS,
        next_retry_at=_past(),
    )
    async with database() as db:
        async with db.begin():
            assert not await WebhookEventRepository(db).claim(delivery_id, MAX_ATTEMPTS)
    row = await _row(database, delivery_id)
    assert row.status == "retryable_error" and row.attempt_count == MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_concurrent_due_retry_is_claimed_by_exactly_one_verifier(
    database: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = _unique("test-race-claim")
    await _seed(
        database, delivery_id, status="retryable_error", attempt_count=1, next_retry_at=_past()
    )

    async def claim() -> bool:
        async with database() as db:
            async with db.begin():
                return await WebhookEventRepository(db).claim(delivery_id, MAX_ATTEMPTS)

    results = await asyncio.gather(claim(), claim(), claim())
    assert sorted(results) == [False, False, True]
    row = await _row(database, delivery_id)
    assert row.status == "verifying" and row.attempt_count == 2


@pytest.mark.asyncio
async def test_concurrent_exhaustion_changes_the_row_exactly_once(
    database: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = _unique("test-race-exhaust")
    await _seed(
        database,
        delivery_id,
        status="retryable_error",
        attempt_count=MAX_ATTEMPTS,
        next_retry_at=_past(),
    )

    async def exhaust() -> bool:
        async with database() as db:
            async with db.begin():
                return await WebhookEventRepository(db).exhaust(delivery_id, MAX_ATTEMPTS)

    assert sorted(await asyncio.gather(exhaust(), exhaust())) == [False, True]
    row = await _row(database, delivery_id)
    assert row.status == "exhausted"
    assert row.failure_code == EXHAUSTED_FAILURE_CODE
    assert row.next_retry_at is None and row.attempt_count == MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_exhausted_event_is_never_reopened_by_claim_or_retry_time(
    database: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = _unique("test-terminal")
    await _seed(
        database,
        delivery_id,
        status="exhausted",
        attempt_count=MAX_ATTEMPTS,
        failure_code=EXHAUSTED_FAILURE_CODE,
        next_retry_at=_past(),
    )
    async with database() as db:
        async with db.begin():
            repo = WebhookEventRepository(db)
            assert not await repo.claim(delivery_id, MAX_ATTEMPTS)
            assert not await repo.claim(delivery_id)
            assert not await repo.exhaust(delivery_id, MAX_ATTEMPTS)
    row = await _row(database, delivery_id)
    assert row.status == "exhausted" and row.attempt_count == MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_stale_verifying_claim_at_budget_end_is_exhausted_not_reclaimed(
    database: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = _unique("test-stale-spent")
    await _seed(
        database,
        delivery_id,
        status="verifying",
        attempt_count=MAX_ATTEMPTS,
        updated_at=datetime.now(UTC) - timedelta(minutes=11),
    )
    async with database() as db:
        async with db.begin():
            repo = WebhookEventRepository(db)
            assert not await repo.claim(delivery_id, MAX_ATTEMPTS)
    async with database() as db:
        async with db.begin():
            assert await WebhookEventRepository(db).exhaust(delivery_id, MAX_ATTEMPTS)
    row = await _row(database, delivery_id)
    assert row.status == "exhausted" and row.attempt_count == MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_stale_verifying_claim_within_budget_is_reclaimed(
    database: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = _unique("test-stale-within")
    await _seed(
        database,
        delivery_id,
        status="verifying",
        attempt_count=2,
        updated_at=datetime.now(UTC) - timedelta(minutes=11),
    )
    async with database() as db:
        async with db.begin():
            assert await WebhookEventRepository(db).claim(delivery_id, MAX_ATTEMPTS)
    row = await _row(database, delivery_id)
    assert row.status == "verifying" and row.attempt_count == 3


@pytest.mark.asyncio
async def test_failure_outcomes_respect_the_budget_boundary(
    database: async_sessionmaker[AsyncSession],
) -> None:
    due = datetime.now(UTC) + timedelta(seconds=90)
    cases = [
        # (starting attempt, retryable, expected status, expected next_retry_at set)
        (MAX_ATTEMPTS - 1, True, "retryable_error", True),
        (MAX_ATTEMPTS, True, "exhausted", False),
        (1, False, "rejected", False),
    ]
    for index, (attempt, retryable, expected, scheduled) in enumerate(cases):
        delivery_id = _unique(f"test-outcome-{index}")
        await _seed(database, delivery_id, status="verifying", attempt_count=attempt)
        async with database() as db:
            async with db.begin():
                outcome = await WebhookEventRepository(db).record_attempt_failure(
                    delivery_id,
                    attempt,
                    retryable=retryable,
                    failure_code="github_api_error",
                    next_retry_at=due,
                    max_attempts=MAX_ATTEMPTS,
                )
        row = await _row(database, delivery_id)
        assert outcome == expected == row.status
        assert (row.next_retry_at is not None) is scheduled


@pytest.mark.asyncio
async def test_superseded_attempt_cannot_overwrite_a_newer_attempt(
    database: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = _unique("test-superseded")
    await _seed(database, delivery_id, status="verifying", attempt_count=3)
    async with database() as db:
        async with db.begin():
            outcome = await WebhookEventRepository(db).record_attempt_failure(
                delivery_id,
                2,  # a stale holder of attempt 2
                retryable=False,
                failure_code="github_api_error",
                next_retry_at=None,
                max_attempts=MAX_ATTEMPTS,
            )
    assert outcome is None
    row = await _row(database, delivery_id)
    assert row.status == "verifying" and row.attempt_count == 3


# --- Route behaviour -------------------------------------------------------------------------


def _signed(payload: dict[str, object]) -> tuple[bytes, str]:
    body = json.dumps(payload).encode()
    signature = (
        "sha256="
        + hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    )
    return body, signature


def _app(database: async_sessionmaker[AsyncSession]) -> FastAPI:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    app = create_app(
        Settings(environment="test", github_app_id=123, github_app_private_key=private)
    )

    async def session() -> AsyncIterator[AsyncSession]:
        async with database() as db:
            yield db

    app.dependency_overrides[get_db_session] = session
    return app


def _payload(installation_id: int, run_id: int) -> dict[str, object]:
    return {
        "action": "completed",
        "installation": {"id": installation_id},
        "repository": {
            "id": 101,
            "full_name": "base/repo",
            "name": "repo",
            "owner": {"login": "base"},
        },
        "workflow_run": {
            "id": run_id,
            "run_attempt": 1,
            "head_sha": "c" * 40,
            "head_branch": "feature",
            "head_repository": {"id": 202, "full_name": "fork/repo"},
            "conclusion": "failure",
        },
    }


@pytest.mark.asyncio
@respx.mock
async def test_full_ladder_exhausts_after_five_attempts_and_stops_calling_github(
    database: async_sessionmaker[AsyncSession],
) -> None:
    app = _app(database)
    run_id = int(datetime.now(UTC).timestamp() * 1_000_000)
    delivery_id = _unique("test-ladder")
    installation_id = 501
    api = respx.get(f"https://api.github.com/app/installations/{installation_id}").respond(
        429, headers={"retry-after": "5"}
    )
    body, signature = _signed(_payload(installation_id, run_id))
    headers = {
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": signature,
        "X-GitHub-Delivery": delivery_id,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for attempt in range(1, MAX_ATTEMPTS):
            response = await client.post("/v1/webhooks/github", content=body, headers=headers)
            assert response.status_code == 503, attempt
            assert int(response.headers["Retry-After"]) >= 1
            await _make_due(database, delivery_id)
        final = await client.post("/v1/webhooks/github", content=body, headers=headers)
        assert final.status_code == 422
        assert final.json()["detail"] == "Delivery verification retries exhausted"
        assert api.call_count == MAX_ATTEMPTS

        row = await _row(database, delivery_id)
        assert row.status == "exhausted" and row.attempt_count == MAX_ATTEMPTS
        assert row.failure_code == EXHAUSTED_FAILURE_CODE and row.next_retry_at is None
        # Exhaustion must not erase the most recent transient cause.
        assert row.last_error_code == "github_rate_limited"

        # Repeated deliveries are idempotent: same response, no GitHub call, no state change.
        for _ in range(3):
            again = await client.post("/v1/webhooks/github", content=body, headers=headers)
            assert again.status_code == 422
            assert again.json()["detail"] == "Delivery verification retries exhausted"
        assert api.call_count == MAX_ATTEMPTS
        after = await _row(database, delivery_id)
        assert after.status == "exhausted" and after.attempt_count == MAX_ATTEMPTS


@pytest.mark.asyncio
@respx.mock
async def test_early_redelivery_is_refused_with_retry_after_and_no_github_call(
    database: async_sessionmaker[AsyncSession],
) -> None:
    app = _app(database)
    run_id = int(datetime.now(UTC).timestamp() * 1_000_000)
    delivery_id = _unique("test-early-detail")
    installation_id = 502
    api = respx.get(f"https://api.github.com/app/installations/{installation_id}").respond(
        429, headers={"retry-after": "120"}
    )
    body, signature = _signed(_payload(installation_id, run_id))
    headers = {
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": signature,
        "X-GitHub-Delivery": delivery_id,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post("/v1/webhooks/github", content=body, headers=headers)
        assert first.status_code == 503
        early = await client.post("/v1/webhooks/github", content=body, headers=headers)
    assert early.status_code == 503
    assert "scheduled for retry" in early.json()["detail"]
    assert 100 <= int(early.headers["Retry-After"]) <= 120
    assert api.call_count == 1
    row = await _row(database, delivery_id)
    assert row.attempt_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_concurrent_due_redeliveries_verify_exactly_once(
    database: async_sessionmaker[AsyncSession],
) -> None:
    app = _app(database)
    run_id = int(datetime.now(UTC).timestamp() * 1_000_000)
    delivery_id = _unique("test-concurrent-due")
    installation_id = 503
    api = respx.get(f"https://api.github.com/app/installations/{installation_id}").respond(
        429, headers={"retry-after": "5"}
    )
    body, signature = _signed(_payload(installation_id, run_id))
    headers = {
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": signature,
        "X-GitHub-Delivery": delivery_id,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post("/v1/webhooks/github", content=body, headers=headers)
        assert first.status_code == 503
        await _make_due(database, delivery_id)
        responses = await asyncio.gather(
            *(client.post("/v1/webhooks/github", content=body, headers=headers) for _ in range(3))
        )
    assert all(item.status_code == 503 for item in responses)
    # One initial attempt plus exactly one verifier for the due retry.
    assert api.call_count == 2
    row = await _row(database, delivery_id)
    assert row.attempt_count == 2


async def _make_due(database: async_sessionmaker[AsyncSession], delivery_id: str) -> None:
    async with database() as db:
        row = await db.get(WebhookEventModel, delivery_id)
        assert row is not None
        row.next_retry_at = _past()
        await db.commit()


@pytest.mark.asyncio
@respx.mock
async def test_provider_delay_beyond_ceiling_is_a_visible_hold_at_the_route(
    database: async_sessionmaker[AsyncSession],
) -> None:
    app = _app(database)
    run_id = int(datetime.now(UTC).timestamp() * 1_000_000)
    delivery_id = _unique("test-route-hold")
    installation_id = 504
    api = respx.get(f"https://api.github.com/app/installations/{installation_id}").respond(
        429, headers={"retry-after": "90000"}
    )
    body, signature = _signed(_payload(installation_id, run_id))
    headers = {
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": signature,
        "X-GitHub-Delivery": delivery_id,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post("/v1/webhooks/github", content=body, headers=headers)
        assert first.status_code == 503
        assert int(first.headers["Retry-After"]) > 86400
        early = await client.post("/v1/webhooks/github", content=body, headers=headers)
    assert early.status_code == 503 and api.call_count == 1
    row = await _row(database, delivery_id)
    assert row.status == "provider_hold" and row.attempt_count == 1
    assert row.next_retry_at is not None


def _success_mocks(installation_id: int, run_id: int, sha: str) -> None:
    event_repo = {"id": 101, "full_name": "base/repo", "name": "repo", "owner": {"login": "base"}}
    source_repo = {"id": 202, "full_name": "fork/repo"}
    run = {
        "id": run_id,
        "run_attempt": 1,
        "head_repository": source_repo,
        "head_sha": sha,
        "head_branch": "feature",
        "conclusion": "failure",
    }
    respx.post(f"https://api.github.com/app/installations/{installation_id}/access_tokens").respond(
        201,
        json={
            "token": "synthetic",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    respx.get("https://api.github.com/installation/repositories").respond(
        200, json={"repositories": [event_repo, source_repo]}
    )
    respx.get(f"https://api.github.com/repos/base/repo/actions/runs/{run_id}").respond(
        200, json={**run, "repository": event_repo}
    )
    respx.get("https://api.github.com/repos/fork/repo").respond(200, json=source_repo)
    respx.get(f"https://api.github.com/repos/fork/repo/commits/{sha}").respond(
        200, json={"sha": sha}
    )


@pytest.mark.asyncio
@respx.mock
async def test_route_failure_then_worker_completes_verification_end_to_end(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """The route persists identity before verifying; the worker finishes from that alone."""
    from src.packages.shared.webhook_worker import RetryWorker, WorkerConfig

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    app = _app(database)
    run_id = int(datetime.now(UTC).timestamp() * 1_000_000)
    delivery_id = _unique("test-e2e")
    installation_id = 505
    sha = "c" * 40
    transient = respx.get(f"https://api.github.com/app/installations/{installation_id}").respond(
        429, headers={"retry-after": "5"}
    )
    body, signature = _signed(_payload(installation_id, run_id))
    headers = {
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": signature,
        "X-GitHub-Delivery": delivery_id,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post("/v1/webhooks/github", content=body, headers=headers)
    assert first.status_code == 503 and transient.call_count == 1

    stored = await _row(database, delivery_id)
    # Identity the worker needs was persisted before verification, and no payload was stored.
    assert stored.installation_id == installation_id
    assert stored.run_id == run_id and stored.source_sha == sha
    assert stored.source_repository_name == "fork/repo"
    assert stored.payload_sha256 == hashlib.sha256(body).hexdigest()

    await _make_due(database, delivery_id)
    # The same installation route now answers successfully; the worker verifies with it.
    transient.respond(200, json={"id": installation_id, "app_id": 123, "suspended_at": None})
    _success_mocks(installation_id, run_id, sha)
    config = WorkerConfig(
        poll_seconds=0.05,
        batch_size=20,
        max_attempts=MAX_ATTEMPTS,
        base_delay_seconds=60,
        max_delay_seconds=900,
        provider_ceiling_seconds=86400,
        db_backoff_max_seconds=0.05,
        verification_deadline_seconds=60.0,
        github_api_url="https://api.github.com",
        github_app_id=123,
        github_app_private_key=private,
    )
    summary = await RetryWorker(database, config).tick()
    assert summary.verified == 1
    final = await _row(database, delivery_id)
    assert final.status == "verified" and final.attempt_count == 2
    assert final.pipeline_id is not None and final.last_error_code is None


@pytest.mark.asyncio
@respx.mock
async def test_route_verification_past_the_deadline_schedules_a_retry(
    database: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.packages.shared.workflow_provenance import WorkflowRunProvenanceVerifier as Verifier

    app = _app(database)
    # Settings enforces a 30-second minimum, so the deadline is shortened on a validated copy.
    app.state.settings = app.state.settings.model_copy(
        update={"webhook_verification_deadline_seconds": 0.2}
    )

    async def hangs(self: object, *args: object, **kwargs: object) -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(Verifier, "verify", hangs)
    run_id = int(datetime.now(UTC).timestamp() * 1_000_000)
    delivery_id = _unique("test-route-deadline")
    body, signature = _signed(_payload(506, run_id))
    headers = {
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": signature,
        "X-GitHub-Delivery": delivery_id,
    }
    started = time.monotonic()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/v1/webhooks/github", content=body, headers=headers)
    assert time.monotonic() - started < 5
    assert response.status_code == 503
    assert int(response.headers["Retry-After"]) >= 1
    row = await _row(database, delivery_id)
    assert row.status == "retryable_error"
    assert row.last_error_code == "verification_deadline_exceeded"


@pytest.mark.asyncio
async def test_concurrent_registration_of_one_delivery_has_exactly_one_winner(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Two concurrent deliveries with the same identifier create one record, never two."""
    delivery_id = _unique("test-race-register")

    async def register() -> bool:
        async with database() as db:
            async with db.begin():
                _, created = await WebhookEventRepository(db).register(
                    delivery_id, "a" * 64, "workflow_run"
                )
                return created

    assert sorted(await asyncio.gather(register(), register(), register())) == [False, False, True]
    async with database() as db:
        rows = await db.execute(
            select(WebhookEventModel).where(WebhookEventModel.delivery_id == delivery_id)
        )
        assert len(rows.scalars().all()) == 1


@pytest.mark.asyncio
async def test_same_delivery_with_different_signed_content_is_a_conflict(
    database: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = _unique("test-conflict")
    async with database() as db:
        async with db.begin():
            await WebhookEventRepository(db).register(delivery_id, "a" * 64, "workflow_run")
    async with database() as db:
        async with db.begin():
            with pytest.raises(DeliveryConflictError):
                await WebhookEventRepository(db).register(delivery_id, "b" * 64, "workflow_run")
