"""Phase 1.10 worker stage against disposable PostgreSQL: persisted provenance gates every request.

GitHub (API and the signed download host) is mocked with respx and DNS resolution is patched, so no
real network is used. The real worker claim, persisted-provenance check, App authentication,
retrieval, archive parsing and persistence run unchanged.
"""

import asyncio
import io
import json
import re
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select, update

from src.packages.database import log_jobs
from src.packages.database.models import PipelineModel
from src.packages.database.session import get_session_factory
from src.packages.sdk import github_actions_logs
from src.packages.shared import webhook_worker
from src.packages.shared.models import FailureContext
from src.packages.shared.persisted_provenance import verify_context_provenance
from src.packages.shared.prompt_builder import DiagnosticPromptBuilder
from src.packages.shared.webhook_worker import LogRetrievalConfig, RetryWorker, WorkerConfig
from src.packages.shared.workflow_logs import LogLimits, retrieve_failure_signal
from tests.integration.seeding import Seeded, seed_incident

APP_ID = 123
SIGNED = "https://results-receiver.actions.githubusercontent.com/logs.zip?sig=SIGNEDSECRET"
LOG = (
    b"2026-11-19T10:00:01.0000000Z FAILED tests/test_calc.py::test_add - AssertionError: no\n"
    b"2026-11-19T10:00:01.0000000Z tests/test_calc.py:7: AssertionError\n"
    b"2026-11-19T10:00:02.0000000Z ##[error]Process completed with exit code 1.\n"
)


def _archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("test/2_Run tests.txt", LOG)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(host: str) -> list[str]:
        return ["140.82.112.21"]

    monkeypatch.setattr(github_actions_logs, "resolve_host", resolve)


def _worker(private_key: str, *, max_attempts: int = 3) -> RetryWorker:
    config = WorkerConfig(
        poll_seconds=0.05,
        batch_size=20,
        max_attempts=5,
        base_delay_seconds=60,
        max_delay_seconds=900,
        provider_ceiling_seconds=86400,
        db_backoff_max_seconds=0.05,
        verification_deadline_seconds=60.0,
        github_api_url="https://api.github.com",
        github_app_id=APP_ID,
        github_app_private_key=private_key,
        log_retrieval=LogRetrievalConfig(
            max_attempts=max_attempts,
            deadline_seconds=30.0,
            lease_seconds=120,
            limits=LogLimits(
                max_compressed_bytes=1_000_000,
                max_entries=50,
                max_total_bytes=5_000_000,
                max_file_bytes=2_000_000,
                max_ratio=100,
                max_parse_chars=100_000,
                allowed_hosts=("actions.githubusercontent.com",),
            ),
        ),
    )
    return RetryWorker(get_session_factory(), config)


async def _only(seeded: Seeded) -> str:
    """The worker is database-wide: park every other pipeline so only this one is due."""
    pipeline_id = f"pipe_{seeded.context.incident_id}"
    async with get_session_factory()() as session:
        await session.execute(
            update(PipelineModel)
            .where(PipelineModel.pipeline_id != pipeline_id)
            .values(log_status="unavailable")
        )
        await session.commit()
    return pipeline_id


async def _row(pipeline_id: str) -> PipelineModel:
    async with get_session_factory()() as session:
        return (
            await session.execute(
                select(PipelineModel).where(PipelineModel.pipeline_id == pipeline_id)
            )
        ).scalar_one()


def _mock_github(seeded: Seeded, *, logs_status: int = 302) -> dict[str, Any]:
    ctx = seeded.context
    recorded: dict[str, Any] = {}
    respx.get("https://api.github.com/app/installations/42").respond(
        200, json={"id": 42, "app_id": APP_ID, "suspended_at": None}
    )

    def token(request: httpx.Request) -> httpx.Response:
        recorded["scope"] = json.loads(request.content)
        expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        return httpx.Response(201, json={"token": "ghs_worker_test", "expires_at": expires})

    respx.post("https://api.github.com/app/installations/42/access_tokens").mock(side_effect=token)
    base = f"https://api.github.com/repos/crlabs-ai/akesis/actions/runs/{ctx.run_id}/attempts/1"
    respx.get(base).respond(
        200,
        json={
            "id": ctx.run_id,
            "run_attempt": 1,
            "head_sha": ctx.commit_sha,
            "repository": {"id": 1001},
            "head_repository": {"id": 1001},
        },
    )
    if logs_status == 302:
        respx.get(f"{base}/logs").respond(302, headers={"location": SIGNED})
    else:
        respx.get(f"{base}/logs").respond(logs_status)

    def download(request: httpx.Request) -> httpx.Response:
        recorded["download_auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=_archive())

    respx.get(re.compile(r"https://results-receiver\.actions\.githubusercontent\.com/.*")).mock(
        side_effect=download
    )
    return recorded


@pytest.mark.asyncio
@respx.mock
async def test_verified_incident_logs_are_extracted_persisted_and_handed_to_gemini(
    private_key: str,
) -> None:
    seeded = await seed_incident("valid")
    pipeline_id = await _only(seeded)
    recorded = _mock_github(seeded)

    summary = await _worker(private_key).tick()

    assert summary.counters.get("logs_extracted") == 1
    row = await _row(pipeline_id)
    assert (row.log_status, row.log_attempts, row.log_failure_code) == ("extracted", 1, None)
    assert recorded["scope"] == {
        "repository_ids": [1001],
        "permissions": {"actions": "read", "metadata": "read"},
    }
    assert recorded["download_auth"] is None  # the signed URL never receives the token
    stored = FailureContext.model_validate_json(row.failure_context_json or "")
    assert stored.signal.target_file == "tests/test_calc.py"
    assert (stored.signal.job_name, stored.signal.step_name) == ("test", "Run tests")
    assert stored.raw_log_excerpt == stored.signal.extracted_snippet
    # Provenance is untouched, so the persisted context still verifies for remediation.
    assert stored.provenance == seeded.context.provenance
    await verify_context_provenance(stored)
    prompt = DiagnosticPromptBuilder.build_user_prompt(stored)
    block = prompt[prompt.index("<untrusted_ci_log") :]
    assert "tests/test_calc.py" in block and "SIGNEDSECRET" not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "historical",
        "unpersisted",
        "rejected_event",
        "unverified_event",
        "stale",
        "wrong_sha",
    ],
)
async def test_unverified_incident_makes_zero_workflow_log_requests(
    case: str, private_key: str
) -> None:
    seeded = await seed_incident(case)
    pipeline_id = await _only(seeded)
    # A scoped router: any request at all would be recorded, and nothing leaks to other tests.
    with respx.mock(assert_all_called=False) as router:
        route = router.route(host__regex=r".*").respond(500)
        summary = await _worker(private_key).tick()

    assert route.call_count == 0
    assert summary.counters.get("logs_rejected") == 1
    row = await _row(pipeline_id)
    assert row.log_status == "unavailable"
    assert (row.log_failure_code or "").startswith("provenance_")


@pytest.mark.asyncio
@respx.mock
async def test_transient_failure_is_retried_with_backoff_then_exhausted(private_key: str) -> None:
    seeded = await seed_incident("valid")
    pipeline_id = await _only(seeded)
    _mock_github(seeded, logs_status=503)
    worker = _worker(private_key, max_attempts=2)

    await worker.tick()
    row = await _row(pipeline_id)
    assert (row.log_status, row.log_attempts, row.log_failure_code) == (
        "pending",
        1,
        "github_error",
    )
    assert row.log_next_attempt_at is not None and row.log_next_attempt_at > datetime.now(UTC)

    for expected_attempts in (2, 2):  # second attempt, then the budget is spent
        async with get_session_factory()() as session:
            await session.execute(
                update(PipelineModel)
                .where(PipelineModel.pipeline_id == pipeline_id)
                .values(log_next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()
        await worker.tick()
        assert (await _row(pipeline_id)).log_attempts == expected_attempts
    row = await _row(pipeline_id)
    assert (row.log_status, row.log_failure_code) == ("unavailable", "log_retrieval_exhausted")


@pytest.mark.asyncio
@respx.mock
async def test_crashed_claim_is_recovered_only_after_its_lease(private_key: str) -> None:
    seeded = await seed_incident("valid")
    pipeline_id = await _only(seeded)
    _mock_github(seeded)
    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        async with session.begin():
            claims, _ = await log_jobs.claim_due(
                session, now=now, batch=5, lease_seconds=120, max_attempts=3
            )
    assert claims == [(pipeline_id, 1)]  # a worker claimed it and then crashed

    await _worker(private_key).tick()  # lease still live: not reclaimed
    assert (await _row(pipeline_id)).log_status == "fetching"

    async with get_session_factory()() as session:
        await session.execute(
            update(PipelineModel)
            .where(PipelineModel.pipeline_id == pipeline_id)
            .values(log_next_attempt_at=now - timedelta(seconds=1))
        )
        await session.commit()
    await _worker(private_key).tick()
    row = await _row(pipeline_id)
    assert (row.log_status, row.log_attempts) == ("extracted", 2)  # the reclaim counted


@pytest.mark.asyncio
async def test_unstarted_claim_is_released_and_stale_writes_are_refused() -> None:
    seeded = await seed_incident("valid")
    pipeline_id = await _only(seeded)
    async with get_session_factory()() as session:
        async with session.begin():
            claims, _ = await log_jobs.claim_due(
                session, now=datetime.now(UTC), batch=5, lease_seconds=120, max_attempts=3
            )
            assert claims == [(pipeline_id, 1)]
            stale = await log_jobs.complete(session, pipeline_id, 7, status="extracted")
            assert stale == log_jobs.LEASE_LOST
            assert await log_jobs.release_unstarted(session, pipeline_id, 1)
    row = await _row(pipeline_id)
    assert (row.log_status, row.log_attempts, row.log_next_attempt_at) == ("pending", 0, None)


# Phase 1.12: hard cancellation, lease expiry and concurrent workers.


def _gated_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[int], asyncio.Event, asyncio.Event]:
    """Count retrievals; each one signals `started` and waits for `release` before running."""
    calls: list[int] = []
    started, release = asyncio.Event(), asyncio.Event()

    async def gated(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        started.set()
        await release.wait()
        return await real(*args, **kwargs)

    real = retrieve_failure_signal
    monkeypatch.setattr(webhook_worker, "retrieve_failure_signal", gated)
    return calls, started, release


async def _expire_lease(pipeline_id: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            update(PipelineModel)
            .where(PipelineModel.pipeline_id == pipeline_id)
            .values(log_next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()


@pytest.mark.asyncio
@respx.mock
async def test_hard_cancelled_retrieval_keeps_its_claim_until_the_lease_expires(
    private_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await seed_incident("valid")
    pipeline_id = await _only(seeded)
    _mock_github(seeded)
    calls, started, _ = _gated_retrieval(monkeypatch)

    task = asyncio.create_task(_worker(private_key).tick())
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = await _row(pipeline_id)
    assert (row.log_status, row.log_attempts) == ("fetching", 1)
    assert row.log_next_attempt_at is not None and row.log_next_attempt_at > datetime.now(UTC)

    # The lease is live: another worker neither claims the row nor reaches GitHub.
    summary = await _worker(private_key).tick()
    assert len(calls) == 1 and not any(
        k.startswith("logs_") and v for k, v in summary.counters.items()
    )
    assert (await _row(pipeline_id)).log_attempts == 1

    # After the lease, recovery is a new, counted attempt that records the result.
    monkeypatch.setattr(webhook_worker, "retrieve_failure_signal", retrieve_failure_signal)
    await _expire_lease(pipeline_id)
    summary = await _worker(private_key).tick()
    row = await _row(pipeline_id)
    assert (row.log_status, row.log_attempts) == ("extracted", 2)
    assert summary.counters.get("logs_extracted") == 1


@pytest.mark.asyncio
async def test_repeated_cancellation_is_bounded_by_the_attempt_budget(
    private_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await seed_incident("valid")
    pipeline_id = await _only(seeded)
    calls, started, _ = _gated_retrieval(monkeypatch)
    worker = _worker(private_key, max_attempts=2)
    for attempt in (1, 2):
        started.clear()
        task = asyncio.create_task(worker.tick())
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await _row(pipeline_id)).log_attempts == attempt
        await _expire_lease(pipeline_id)
    summary = await worker.tick()
    row = await _row(pipeline_id)
    assert (row.log_status, row.log_failure_code) == ("unavailable", "log_retrieval_exhausted")
    assert len(calls) == 2 and summary.counters.get("logs_exhausted") == 1


@pytest.mark.asyncio
@respx.mock
async def test_concurrent_workers_retrieve_a_row_once(
    private_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await seed_incident("valid")
    pipeline_id = await _only(seeded)
    _mock_github(seeded)
    calls, started, release = _gated_retrieval(monkeypatch)

    first = asyncio.create_task(_worker(private_key).tick())
    await asyncio.wait_for(started.wait(), 5)  # worker A holds a live claim mid-retrieval
    others = await asyncio.gather(*(_worker(private_key).tick() for _ in range(3)))
    release.set()
    summary = await first
    assert len(calls) == 1
    assert summary.counters.get("logs_extracted") == 1
    assert all(o.counters.get("logs_extracted") is None for o in others)
    row = await _row(pipeline_id)
    assert (row.log_status, row.log_attempts) == ("extracted", 1)


@pytest.mark.asyncio
async def test_a_claim_locked_by_another_transaction_is_skipped() -> None:
    seeded = await seed_incident("valid")
    pipeline_id = await _only(seeded)
    async with get_session_factory()() as holder:
        async with holder.begin():  # worker A has selected the row but not yet committed
            held, _ = await log_jobs.claim_due(
                holder, now=datetime.now(UTC), batch=5, lease_seconds=120, max_attempts=3
            )
            assert held == [(pipeline_id, 1)]
            async with get_session_factory()() as other:
                async with other.begin():
                    claimed, _ = await log_jobs.claim_due(
                        other, now=datetime.now(UTC), batch=5, lease_seconds=120, max_attempts=3
                    )
            assert claimed == []
    assert (await _row(pipeline_id)).log_attempts == 1


@pytest.mark.asyncio
@respx.mock
async def test_late_result_after_lease_expiry_is_refused_and_not_counted(
    private_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await seed_incident("valid")
    pipeline_id = await _only(seeded)
    _mock_github(seeded)
    calls, started, release = _gated_retrieval(monkeypatch)

    slow = asyncio.create_task(_worker(private_key).tick())  # attempt 1 stalls mid-retrieval
    await asyncio.wait_for(started.wait(), 5)
    await _expire_lease(pipeline_id)
    monkeypatch.setattr(webhook_worker, "retrieve_failure_signal", retrieve_failure_signal)
    recovered = await _worker(private_key).tick()  # attempt 2 reclaims and records
    assert recovered.counters.get("logs_extracted") == 1
    after_recovery = await _row(pipeline_id)

    release.set()
    late = await slow
    assert late.counters.get("logs_extracted") is None
    assert late.counters.get("logs_lease_lost") == 1
    row = await _row(pipeline_id)
    assert (row.log_status, row.log_attempts, row.failure_context_json) == (
        "extracted",
        2,
        after_recovery.failure_context_json,
    )
    assert len(calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_context_changed_during_retrieval_is_refused_and_not_counted(
    private_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await seed_incident("valid")
    pipeline_id = await _only(seeded)
    _mock_github(seeded)
    _, started, release = _gated_retrieval(monkeypatch)

    task = asyncio.create_task(_worker(private_key).tick())
    await asyncio.wait_for(started.wait(), 5)
    changed = seeded.context.model_copy(update={"workflow_name": "changed"}).model_dump_json()
    async with get_session_factory()() as session:
        await session.execute(
            update(PipelineModel)
            .where(PipelineModel.pipeline_id == pipeline_id)
            .values(failure_context_json=changed)
        )
        await session.commit()
    release.set()
    summary = await task
    assert summary.counters.get("logs_extracted") is None
    assert summary.counters.get("logs_context_changed") == 1
    row = await _row(pipeline_id)
    # Still this attempt's claim: retried after the lease, against the current context.
    assert (row.log_status, row.log_attempts, row.failure_context_json) == ("fetching", 1, changed)
