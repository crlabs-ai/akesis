"""Durable retry worker against disposable PostgreSQL.

Requires AKESIS_DISPOSABLE_TEST_DATABASE_URL pointing at a loopback akesis_test database. GitHub is
mocked with respx; the real verifier, claim, admission and state transitions run.
"""

import asyncio
import os
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import delete
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.packages.database.models import PipelineModel, WebhookEventModel
from src.packages.database.webhook_events import EXHAUSTED_FAILURE_CODE, WebhookEventRepository
from src.packages.shared import webhook_worker
from src.packages.shared.remediation_orchestrator import RemediationOrchestrator
from src.packages.shared.webhook_admission import REMEDIATION_DISABLED_REASON
from src.packages.shared.webhook_worker import RetryWorker, WorkerConfig
from src.packages.shared.workflow_provenance import (
    ProvenanceVerificationError,
    WorkflowRunProvenanceVerifier,
)

MAX_ATTEMPTS = 5
APP_ID = 123


@pytest.fixture
async def database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("AKESIS_DISPOSABLE_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("Explicit disposable PostgreSQL test database not configured")
    parsed = urlsplit(url)
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.path != "/akesis_test":
        pytest.fail("Refusing a database other than the explicitly designated local test database")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    # The worker is database-wide, so each test starts from an empty delivery table. This only
    # ever runs on the disposable database verified above.
    async with sessions() as db:
        await db.execute(delete(WebhookEventModel))
        await db.commit()
    yield sessions
    await engine.dispose()


@pytest.fixture(scope="module")
def private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


def _config(private_key: str, **overrides: object) -> WorkerConfig:
    values: dict[str, object] = {
        "poll_seconds": 0.05,
        "batch_size": 20,
        "max_attempts": MAX_ATTEMPTS,
        "base_delay_seconds": 60,
        "max_delay_seconds": 900,
        "provider_ceiling_seconds": 86400,
        "db_backoff_max_seconds": 0.05,
        "verification_deadline_seconds": 60.0,
        "github_api_url": "https://api.github.com",
        "github_app_id": APP_ID,
        "github_app_private_key": private_key,
    }
    values.update(overrides)
    return WorkerConfig(**values)  # type: ignore[arg-type]


def _unique(prefix: str) -> str:
    return f"{prefix}-{time.time_ns()}"


async def _seed(
    database: async_sessionmaker[AsyncSession],
    delivery_id: str,
    *,
    installation_id: int | None = 404,
    run_id: int | None = None,
    **values: object,
) -> int:
    """Create an identity-complete delivery and set the precise starting state."""
    run = run_id if run_id is not None else time.time_ns() // 1000
    async with database() as db:
        async with db.begin():
            await WebhookEventRepository(db).register(delivery_id, "a" * 64, "workflow_run")
    async with database() as db:
        row = await db.get(WebhookEventModel, delivery_id)
        assert row is not None
        row.action = "completed"
        row.installation_id = installation_id
        row.event_repository_id = 101
        row.event_repository_name = "base/repo"
        row.run_id = run
        row.run_attempt = 2
        row.source_repository_id = 202
        row.source_repository_name = "fork/repo"
        row.source_sha = "a" * 40
        row.source_branch = "feature"
        row.workflow_name = "CI"
        row.run_url = "https://github.com/base/repo/actions/runs/1"
        row.conclusion = "failure"
        for key, value in values.items():
            setattr(row, key, value)
        await db.commit()
    return run


async def _row(database: async_sessionmaker[AsyncSession], delivery_id: str) -> WebhookEventModel:
    async with database() as db:
        row = await db.get(WebhookEventModel, delivery_id, populate_existing=True)
    assert row is not None
    return row


def _past() -> datetime:
    return datetime.now(UTC) - timedelta(seconds=1)


def _mock_installation_429(installation_id: int, retry_after: str = "5") -> respx.Route:
    return respx.get(f"https://api.github.com/app/installations/{installation_id}").respond(
        429, headers={"retry-after": retry_after}
    )


def _mock_successful_verification(installation_id: int, run_id: int) -> None:
    event_repo = {"id": 101, "full_name": "base/repo", "name": "repo", "owner": {"login": "base"}}
    source_repo = {"id": 202, "full_name": "fork/repo"}
    run = {
        "id": run_id,
        "run_attempt": 2,
        "head_repository": source_repo,
        "head_sha": "a" * 40,
        "head_branch": "feature",
        "conclusion": "failure",
    }
    respx.get(f"https://api.github.com/app/installations/{installation_id}").respond(
        200, json={"id": installation_id, "app_id": APP_ID, "suspended_at": None}
    )
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
    respx.get(f"https://api.github.com/repos/fork/repo/commits/{'a' * 40}").respond(
        200, json={"sha": "a" * 40}
    )


def _stale(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) - timedelta(minutes=11)


# --- Concurrency and duplicate-call protection -----------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_concurrent_workers_verify_each_due_delivery_exactly_once(
    database: async_sessionmaker[AsyncSession], private_key: str
) -> None:
    routes = []
    ids = []
    for index in range(4):
        installation_id = 6000 + index
        delivery_id = _unique("test-worker-concurrent")
        ids.append(delivery_id)
        await _seed(
            database,
            delivery_id,
            installation_id=installation_id,
            status="retryable_error",
            attempt_count=1,
            next_retry_at=_past(),
        )
        routes.append(_mock_installation_429(installation_id))

    first = RetryWorker(database, _config(private_key))
    second = RetryWorker(database, _config(private_key))
    summaries = await asyncio.gather(first.tick(), second.tick(), first.tick())

    assert sum(item.claimed for item in summaries) == 4
    for route in routes:
        assert route.call_count == 1, "a due delivery was verified more than once"
    for delivery_id in ids:
        row = await _row(database, delivery_id)
        assert row.attempt_count == 2


# --- Success path, hold and admission --------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_worker_success_admits_without_running_remediation(
    database: async_sessionmaker[AsyncSession],
    private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("remediation must not run while the hold is active")

    monkeypatch.setattr(RemediationOrchestrator, "process_failure", forbidden)
    delivery_id = _unique("test-worker-success")
    run_id = time.time_ns() // 1000
    await _seed(
        database,
        delivery_id,
        installation_id=7001,
        run_id=run_id,
        status="retryable_error",
        attempt_count=1,
        next_retry_at=_past(),
    )
    _mock_successful_verification(7001, run_id)

    summary = await RetryWorker(database, _config(private_key)).tick()

    assert summary.verified == 1
    row = await _row(database, delivery_id)
    assert row.status == "verified" and row.attempt_count == 2
    assert row.last_error_code is None and row.pipeline_id is not None
    async with database() as db:
        pipeline = await db.get(PipelineModel, row.pipeline_id)
    assert pipeline is not None
    assert pipeline.failure_reason == REMEDIATION_DISABLED_REASON


# --- Crash handling and stale recovery -------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_crash_after_claim_is_recovered_after_the_stale_window(
    database: async_sessionmaker[AsyncSession],
    private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery_id = _unique("test-crash-after-claim")
    await _seed(
        database,
        delivery_id,
        installation_id=7002,
        status="retryable_error",
        attempt_count=1,
        next_retry_at=_past(),
    )
    original = webhook_worker.build_verification_payload

    def crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated crash after claim")

    monkeypatch.setattr(webhook_worker, "build_verification_payload", crash)
    with pytest.raises(RuntimeError):
        await RetryWorker(database, _config(private_key)).tick()
    stuck = await _row(database, delivery_id)
    assert stuck.status == "verifying" and stuck.attempt_count == 2

    # Before the stale window nothing reclaims it.
    monkeypatch.setattr(webhook_worker, "build_verification_payload", original)
    assert (await RetryWorker(database, _config(private_key)).tick()).claimed == 0

    async with database() as db:
        row = await db.get(WebhookEventModel, delivery_id)
        assert row is not None
        row.updated_at = _stale()
        await db.commit()
    route = _mock_installation_429(7002)
    summary = await RetryWorker(database, _config(private_key)).tick()
    assert summary.claimed == 1 and route.call_count == 1
    recovered = await _row(database, delivery_id)
    # The crashed claim counted as an attempt, because verification may have started.
    assert recovered.attempt_count == 3 and recovered.status == "retryable_error"


@pytest.mark.asyncio
@respx.mock
async def test_crash_during_verification_is_counted_and_recovered(
    database: async_sessionmaker[AsyncSession],
    private_key: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    delivery_id = _unique("test-crash-during")
    await _seed(
        database,
        delivery_id,
        installation_id=7003,
        status="retryable_error",
        attempt_count=1,
        next_retry_at=_past(),
    )

    async def boom(self: object, *args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated crash during verification")

    monkeypatch.setattr(WorkflowRunProvenanceVerifier, "verify", boom)
    summary = await RetryWorker(database, _config(private_key)).tick()

    assert summary.crashed == 1
    stuck = await _row(database, delivery_id)
    assert stuck.status == "verifying" and stuck.attempt_count == 2
    # The interruption is logged by exception type only, never by message.
    assert "simulated crash" not in capsys.readouterr().out

    async with database() as db:
        row = await db.get(WebhookEventModel, delivery_id)
        assert row is not None
        row.updated_at = _stale()
        await db.commit()
    monkeypatch.undo()
    route = _mock_installation_429(7003)
    await RetryWorker(database, _config(private_key)).tick()
    assert route.call_count == 1
    assert (await _row(database, delivery_id)).attempt_count == 3


# --- Attempt budget, exhaustion and provider holds ------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_stale_claim_at_budget_end_is_exhausted_without_calling_github(
    database: async_sessionmaker[AsyncSession], private_key: str
) -> None:
    delivery_id = _unique("test-stale-budget")
    await _seed(
        database,
        delivery_id,
        installation_id=7004,
        status="verifying",
        attempt_count=MAX_ATTEMPTS,
        updated_at=_stale(),
    )
    route = _mock_installation_429(7004)

    summary = await RetryWorker(database, _config(private_key)).tick()

    assert summary.exhausted == 1 and route.call_count == 0
    row = await _row(database, delivery_id)
    assert row.status == "exhausted" and row.failure_code == EXHAUSTED_FAILURE_CODE
    assert row.attempt_count == MAX_ATTEMPTS and row.next_retry_at is None


@pytest.mark.asyncio
@respx.mock
async def test_final_attempt_failure_exhausts_and_later_ticks_leave_it_alone(
    database: async_sessionmaker[AsyncSession], private_key: str
) -> None:
    delivery_id = _unique("test-ladder-worker")
    await _seed(
        database,
        delivery_id,
        installation_id=7005,
        status="retryable_error",
        attempt_count=MAX_ATTEMPTS - 1,
        next_retry_at=_past(),
    )
    route = _mock_installation_429(7005)
    worker = RetryWorker(database, _config(private_key))

    first = await worker.tick()
    assert first.exhausted == 1 and route.call_count == 1
    row = await _row(database, delivery_id)
    assert row.status == "exhausted" and row.last_error_code == "github_rate_limited"

    later = await worker.tick()
    assert later.examined == 0 and later.claimed == 0
    assert route.call_count == 1
    assert (await _row(database, delivery_id)).status == "exhausted"


@pytest.mark.asyncio
@respx.mock
async def test_provider_delay_beyond_ceiling_is_held_then_released_when_due(
    database: async_sessionmaker[AsyncSession], private_key: str
) -> None:
    delivery_id = _unique("test-provider-hold")
    await _seed(
        database,
        delivery_id,
        installation_id=7006,
        status="retryable_error",
        attempt_count=1,
        next_retry_at=_past(),
    )
    route = _mock_installation_429(7006, retry_after="90000")
    worker = RetryWorker(database, _config(private_key))

    held = await worker.tick()
    assert held.held == 1
    row = await _row(database, delivery_id)
    assert row.status == "provider_hold" and row.attempt_count == 2
    # The full provider delay is kept, not shortened to the local backoff.
    assert row.next_retry_at is not None
    remaining = (row.next_retry_at.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
    assert 89_000 <= remaining <= 90_100

    # Not claimable before its due time, so no GitHub call and no attempt is consumed.
    assert (await worker.tick()).claimed == 0
    assert route.call_count == 1

    async with database() as db:
        stored = await db.get(WebhookEventModel, delivery_id)
        assert stored is not None
        stored.next_retry_at = _past()
        await db.commit()
    released = await worker.tick()
    assert released.claimed == 1 and route.call_count == 2
    assert (await _row(database, delivery_id)).attempt_count == 3


@pytest.mark.asyncio
async def test_identity_incomplete_delivery_is_rejected_not_guessed(
    database: async_sessionmaker[AsyncSession], private_key: str
) -> None:
    delivery_id = _unique("test-identity-gap")
    await _seed(
        database,
        delivery_id,
        installation_id=None,
        status="retryable_error",
        attempt_count=1,
        next_retry_at=_past(),
    )
    summary = await RetryWorker(database, _config(private_key)).tick()
    assert summary.rejected == 1
    row = await _row(database, delivery_id)
    assert row.status == "rejected" and row.failure_code == "identity_incomplete"


# --- Availability and shutdown ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_claims_while_github_app_is_not_configured(
    database: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = _unique("test-unconfigured")
    await _seed(
        database,
        delivery_id,
        installation_id=7007,
        status="retryable_error",
        attempt_count=1,
        next_retry_at=_past(),
    )
    summary = await RetryWorker(database, _config("")).tick()
    assert summary.app_not_configured and summary.claimed == 0
    row = await _row(database, delivery_id)
    assert row.status == "retryable_error" and row.attempt_count == 1


@pytest.mark.asyncio
async def test_database_outage_backs_off_and_the_loop_survives(
    private_key: str,
) -> None:
    unreachable = create_async_engine(
        "postgresql+asyncpg://postgres:unused@127.0.0.1:9/akesis_test"
    )
    states: list[str] = []
    stop = asyncio.Event()
    worker = RetryWorker(async_sessionmaker(unreachable), _config(private_key))
    task = asyncio.create_task(worker.run_forever(stop, states.append))
    await asyncio.sleep(0.4)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    await unreachable.dispose()

    assert "database_unavailable" in states
    assert states[-1] == "stopped"
    assert "error" not in states


@pytest.mark.asyncio
@respx.mock
async def test_recovery_after_outage_resumes_processing(
    database: async_sessionmaker[AsyncSession], private_key: str
) -> None:
    delivery_id = _unique("test-recovery")
    await _seed(
        database,
        delivery_id,
        installation_id=7008,
        status="retryable_error",
        attempt_count=1,
        next_retry_at=_past(),
    )
    route = _mock_installation_429(7008)
    unreachable = create_async_engine(
        "postgresql+asyncpg://postgres:unused@127.0.0.1:9/akesis_test"
    )
    worker = RetryWorker(async_sessionmaker(unreachable), _config(private_key))
    with pytest.raises((OperationalError, InterfaceError, OSError)):
        await worker.tick()
    await unreachable.dispose()
    # Nothing was claimed during the outage, so no attempt was consumed.
    assert (await _row(database, delivery_id)).attempt_count == 1

    worker._sessions = database  # the database is back
    summary = await worker.tick()
    assert summary.claimed == 1 and route.call_count == 1
    assert (await _row(database, delivery_id)).attempt_count == 2


@pytest.mark.asyncio
async def test_shutdown_before_start_claims_nothing(
    database: async_sessionmaker[AsyncSession], private_key: str
) -> None:
    delivery_id = _unique("test-shutdown-before")
    await _seed(
        database,
        delivery_id,
        installation_id=7009,
        status="retryable_error",
        attempt_count=1,
        next_retry_at=_past(),
    )
    stop = asyncio.Event()
    stop.set()
    states: list[str] = []
    await RetryWorker(database, _config(private_key, poll_seconds=60)).run_forever(
        stop, states.append
    )
    assert states == ["stopped"]
    assert (await _row(database, delivery_id)).attempt_count == 1


@pytest.mark.asyncio
async def test_shutdown_during_sleep_returns_promptly(
    database: async_sessionmaker[AsyncSession], private_key: str
) -> None:
    stop = asyncio.Event()
    states: list[str] = []
    worker = RetryWorker(database, _config(private_key, poll_seconds=60))
    task = asyncio.create_task(worker.run_forever(stop, states.append))
    await asyncio.sleep(0.2)
    started = time.monotonic()
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert time.monotonic() - started < 2
    assert states[-1] == "stopped"


def test_worker_module_has_no_path_to_remediation() -> None:
    # Structural guard: the worker must reach downstream only through the shared admission path.
    source = Path(webhook_worker.__file__).read_text(encoding="utf-8")
    for forbidden in ("remediation_orchestrator", "RemediationOrchestrator", "process_failure"):
        assert forbidden not in source


@pytest.mark.asyncio
@respx.mock
async def test_shutdown_mid_batch_completes_the_in_flight_delivery_and_releases_the_rest(
    database: async_sessionmaker[AsyncSession],
    private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown finishes the delivery being verified, starts no more, and releases the rest.

    The released claims return to the schedule without consuming an attempt, so another worker can
    take them at once instead of waiting out the stale-claim window.
    """
    ids = []
    routes = []
    for index in range(3):
        delivery_id = _unique("test-shutdown-mid")
        ids.append(delivery_id)
        await _seed(
            database,
            delivery_id,
            installation_id=7100 + index,
            status="retryable_error",
            attempt_count=1,
            next_retry_at=_past(),
        )
        routes.append(_mock_installation_429(7100 + index))

    stop = asyncio.Event()
    worker = RetryWorker(database, _config(private_key))
    original = RetryWorker._process
    started = 0

    async def stop_after_first(self: RetryWorker, *args: object, **kwargs: object) -> None:
        nonlocal started
        started += 1
        await original(self, *args, **kwargs)  # type: ignore[arg-type]
        stop.set()  # shutdown requested after the first delivery finished

    monkeypatch.setattr(RetryWorker, "_process", stop_after_first)
    summary = await worker.tick(stop)

    assert summary.claimed == 3 and started == 1
    assert summary.released == 2 and summary.release_failed == 0
    assert routes[0].call_count == 1
    assert routes[1].call_count == 0 and routes[2].call_count == 0, (
        "a released delivery was verified"
    )
    first = await _row(database, ids[0])
    assert first.status == "retryable_error" and first.attempt_count == 2
    for delivery_id in ids[1:]:
        row = await _row(database, delivery_id)
        assert row.status == "retryable_error", "a released claim was left in verifying"
        assert row.attempt_count == 1, "a released, never-started attempt consumed budget"
        assert row.next_retry_at is not None and row.next_retry_at <= datetime.now(UTC)


@pytest.mark.asyncio
@respx.mock
async def test_released_deliveries_are_claimable_by_another_worker_without_double_counting(
    database: async_sessionmaker[AsyncSession],
    private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = [_unique("test-release-handoff") for _ in range(2)]
    for index, delivery_id in enumerate(ids):
        await _seed(
            database,
            delivery_id,
            installation_id=7120 + index,
            status="retryable_error",
            attempt_count=1,
            next_retry_at=_past(),
        )
    stop = asyncio.Event()
    stop.set()  # shutdown before the first delivery starts
    original_claim = WebhookEventRepository.claim

    first = await RetryWorker(database, _config(private_key)).tick(stop)
    assert first.claimed == 0  # stop set before the batch: nothing claimed at all
    assert first.released == 0

    # Now a worker claims a batch, is stopped before processing, and releases both claims.
    stop_after_claim = asyncio.Event()

    async def claim_and_request_stop(
        self: WebhookEventRepository, *args: object, **kwargs: object
    ) -> bool:
        result = await original_claim(self, *args, **kwargs)  # type: ignore[arg-type]
        stop_after_claim.set()
        return result

    monkeypatch.setattr(WebhookEventRepository, "claim", claim_and_request_stop)
    routes = [_mock_installation_429(7120 + index) for index in range(2)]
    released = await RetryWorker(database, _config(private_key)).tick(stop_after_claim)
    assert released.claimed == 2 and released.released == 2
    monkeypatch.setattr(WebhookEventRepository, "claim", original_claim)
    assert all(route.call_count == 0 for route in routes)
    for delivery_id in ids:
        assert (await _row(database, delivery_id)).attempt_count == 1

    # Another worker (not stopping) takes the released deliveries straight away.
    handoff = await RetryWorker(database, _config(private_key)).tick()
    assert handoff.claimed == 2 and handoff.retried == 2
    for delivery_id in ids:
        assert (await _row(database, delivery_id)).attempt_count == 2


@pytest.mark.asyncio
async def test_shutdown_during_database_failure_does_not_crash_and_leaves_stale_recovery(
    database: async_sessionmaker[AsyncSession],
    private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.exc import OperationalError

    ids = [_unique("test-release-db-down") for _ in range(2)]
    for index, delivery_id in enumerate(ids):
        await _seed(
            database,
            delivery_id,
            installation_id=7130 + index,
            status="retryable_error",
            attempt_count=1,
            next_retry_at=_past(),
        )
    stop = asyncio.Event()
    original_claim = WebhookEventRepository.claim

    async def claim_and_request_stop(
        self: WebhookEventRepository, *args: object, **kwargs: object
    ) -> bool:
        result = await original_claim(self, *args, **kwargs)  # type: ignore[arg-type]
        stop.set()
        return result

    async def database_down(self: WebhookEventRepository, *args: object) -> bool:
        raise OperationalError("release", {}, OSError("connection refused"))

    monkeypatch.setattr(WebhookEventRepository, "claim", claim_and_request_stop)
    monkeypatch.setattr(WebhookEventRepository, "release_unstarted", database_down)
    summary = await RetryWorker(database, _config(private_key)).tick(stop)

    assert summary.release_failed == 2 and summary.released == 0
    # The claims are left in verifying; the stale-claim window is the fallback.
    for delivery_id in ids:
        row = await _row(database, delivery_id)
        assert row.status == "verifying" and row.attempt_count == 2


@pytest.mark.asyncio
async def test_verification_past_the_deadline_is_a_retryable_failure_not_a_stranded_claim(
    database: async_sessionmaker[AsyncSession],
    private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery_id = _unique("test-deadline-worker")
    await _seed(
        database,
        delivery_id,
        installation_id=7200,
        status="retryable_error",
        attempt_count=1,
        next_retry_at=_past(),
    )

    async def hangs(self: object, *args: object, **kwargs: object) -> None:
        await asyncio.sleep(30)  # longer than the deadline below

    monkeypatch.setattr(WorkflowRunProvenanceVerifier, "verify", hangs)
    started = time.monotonic()
    summary = await RetryWorker(
        database, _config(private_key, verification_deadline_seconds=0.2)
    ).tick()

    assert time.monotonic() - started < 5, "deadline did not bound the verification"
    assert summary.retried == 1 and summary.crashed == 0
    row = await _row(database, delivery_id)
    assert row.status == "retryable_error"
    assert row.last_error_code == "verification_deadline_exceeded"
    assert row.attempt_count == 2 and row.next_retry_at is not None


@pytest.mark.asyncio
@respx.mock
async def test_watchdog_pings_only_at_real_progress_not_during_an_in_flight_verification(
    database: async_sessionmaker[AsyncSession],
    private_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung verification must produce no ping, so systemd can act on it."""
    delivery_id = _unique("test-watchdog")
    await _seed(
        database,
        delivery_id,
        installation_id=7300,
        status="retryable_error",
        attempt_count=1,
        next_retry_at=_past(),
    )
    pings: list[float] = []

    def recording_notify(message: str) -> bool:
        pings.append(time.monotonic())
        return True

    monkeypatch.setattr(webhook_worker, "notify", recording_notify)
    pings_at_start: list[int] = []

    async def observed_verify(self: object, *args: object, **kwargs: object) -> None:
        pings_at_start.append(len(pings))  # pings before this verification started
        await asyncio.sleep(0.5)  # a verification that is still in flight
        assert len(pings) == pings_at_start[0], "a ping was sent during an in-flight verification"
        raise ProvenanceVerificationError("simulated", code="github_api_error")

    monkeypatch.setattr(WorkflowRunProvenanceVerifier, "verify", observed_verify)
    await RetryWorker(database, _config(private_key)).tick(
        None, progress=lambda: pings.append(time.monotonic())
    )
    assert len(pings) == pings_at_start[0] + 1, "progress was not reported after the delivery"
