"""Phase 1.14c against disposable PostgreSQL: approved remediation runs only in the worker.

The API records the decision and queues one durable job; the worker claims it with a lease and
calls the gated `resume_approval`. With the real orchestrator the unconditional hold refuses first,
and the job is parked as `held`. Post-hold behaviour (completion, retries, leases, concurrency) is
driven by an orchestrator double, and real binding checks by a hold-bypassing test adapter.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from src.apps.api.main import create_app
from src.packages.database import approval_jobs
from src.packages.database.models import (
    ApprovalModel,
    GitHubInstallationModel,
    PipelineModel,
    WebhookEventModel,
    WorkspaceMembershipModel,
)
from src.packages.database.repositories import ApprovalRepository
from src.packages.database.session import get_session_factory
from src.packages.sdk.remote_sandbox_runner import RemoteSandboxRunner
from src.packages.sdk.sandbox_runner import DockerUnavailableError
from src.packages.shared import config as config_module
from src.packages.shared.approval_service import approval_service
from src.packages.shared.config import Settings, settings
from src.packages.shared.models import ApprovalStatus, PipelineStatus
from src.packages.shared.mutation_service import GitMutationService
from src.packages.shared.persisted_provenance import ProvenanceRejected, verify_approval_provenance
from src.packages.shared.remediation_orchestrator import (
    OrchestrationError,
    RemediationOrchestrator,
    default_approver_authority,
)
from src.packages.shared.source_snapshot import SnapshotService
from src.packages.shared.webhook_worker import (
    ApprovalExecutionConfig,
    RetryWorker,
    TickSummary,
    WorkerConfig,
)
from tests.integration.seeding import SEEDED_INSTALLATION_ID, Seeded, seed_incident
from tests.integration.test_approval_flow import create_slack_signature
from tests.support.tenancy import add_member, reset_identity, seed_approver_workspace

APPROVER = "U12345"
REPO = Path(__file__).resolve().parents[2]
_REAL_RUN = subprocess.run  # kept before any test patches subprocess.run


@pytest.fixture(autouse=True)
async def approvers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "slack_approver_user_ids", [APPROVER])
    # ADR-0012: the seeded installation's workspace maps APPROVER to an approver member.
    async with get_session_factory()() as db:
        await reset_identity(db)
        await seed_approver_workspace(db, SEEDED_INSTALLATION_ID, APPROVER)


class Orchestrator:
    """Records calls. Returns `outcome` (a pipeline status), or raises it if it is an exception."""

    def __init__(self, outcome: Any = PipelineStatus.COMPLETED, gate: asyncio.Event | None = None):
        self.outcome = outcome
        self.gate = gate
        self.calls: list[str] = []
        self.started = asyncio.Event()

    async def resume_approval(self, approval_id: str) -> Any:
        self.calls.append(approval_id)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return SimpleNamespace(status=self.outcome)


class Unchecked:
    """Real persisted-provenance and binding checks, without the hold (test-only adapter)."""

    def __init__(self) -> None:
        self.inner = RemediationOrchestrator()

    async def resume_approval(self, approval_id: str) -> Any:
        await verify_approval_provenance(approval_id)
        return await self.inner._resume_approval_unchecked(approval_id)


def _worker(orchestrator: Any = None, *, max_attempts: int = 3, lease: int = 120) -> RetryWorker:
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
        github_app_id=123,
        github_app_private_key="synthetic",
        approval_execution=ApprovalExecutionConfig(
            max_attempts=max_attempts, deadline_seconds=30.0, lease_seconds=lease
        ),
    )
    return RetryWorker(get_session_factory(), config, orchestrator=orchestrator)


async def _stage(worker: RetryWorker, stop: asyncio.Event | None = None, **kw: Any) -> TickSummary:
    summary = TickSummary()
    await worker._approval_stage(stop, kw.get("progress"), summary)
    return summary


async def _approve(seeded: Seeded, reviewer: str = APPROVER) -> str:
    """Record an approval exactly as the API does, and park every other queued job."""
    approval_id = seeded.approval.approval_id
    async with get_session_factory()() as session:
        record, duplicate = await ApprovalRepository(session).record_decision(
            approval_id, ApprovalStatus.APPROVED, reviewer=reviewer
        )
    assert record is not None and record.status == ApprovalStatus.APPROVED and not duplicate
    await _only(approval_id)
    return approval_id


async def _only(approval_id: str) -> None:
    """The worker is database-wide: park every other queued job so only this one is due."""
    async with get_session_factory()() as session:
        await session.execute(
            update(ApprovalModel)
            .where(
                ApprovalModel.approval_id != approval_id,
                ApprovalModel.execution_status.in_(("pending", "running", "held")),
            )
            .values(execution_status="completed")
        )
        await session.commit()


async def _job(approval_id: str) -> ApprovalModel:
    async with get_session_factory()() as session:
        return (
            await session.execute(
                select(ApprovalModel).where(ApprovalModel.approval_id == approval_id)
            )
        ).scalar_one()


async def _expire(approval_id: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            update(ApprovalModel)
            .where(ApprovalModel.approval_id == approval_id)
            .values(execution_next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()


def _state(job: ApprovalModel) -> tuple[Any, ...]:
    return (job.execution_status, job.execution_attempts, job.execution_failure_code)


# --- the API only records and queues -------------------------------------------------------------


class _Slack:
    async def update_approval_card(self, response_url: str, approval: Any) -> bool:
        return True


async def _slack(approval_id: str, *, user: str = APPROVER, action: str = "approve_fix") -> Any:
    app = create_app(
        Settings(
            environment="test",
            slack_approver_user_ids=[APPROVER],
            slack_workspace_id="T1",
            slack_channel_id="C1",
        )
    )
    payload = {
        "actions": [{"action_id": action, "value": approval_id}],
        "user": {"id": user},
        "team": {"id": "T1"},
        "channel": {"id": "C1"},
        "response_url": "https://hooks.slack.com/actions/test",
    }
    body = urlencode({"payload": json.dumps(payload)}).encode()
    stamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Signature": create_slack_signature(body, stamp, settings.slack_signing_secret),
        "X-Slack-Request-Timestamp": stamp,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/v1/slack/interactions", content=body, headers=headers)


@pytest.mark.asyncio
async def test_slack_approval_is_recorded_and_queues_exactly_one_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(approval_service, "slack_client", _Slack())
    seeded = await seed_incident("valid")
    approval_id = seeded.approval.approval_id

    first = await _slack(approval_id)
    assert first.status_code == 200 and first.json()["is_duplicate"] is False
    job = await _job(approval_id)
    assert (job.status, job.reviewer) == ("approved", APPROVER)  # actor and decision recorded
    assert _state(job) == ("pending", 0, None)  # queued, not executed
    assert (job.incident_id, job.proposal_id, job.commit_sha) == (
        seeded.context.incident_id,
        seeded.proposal.proposal_id,
        seeded.context.commit_sha,
    )

    for _ in range(3):  # Slack retries the delivery: no second job, nothing re-queued
        again = await _slack(approval_id)
        assert again.status_code == 200 and again.json()["is_duplicate"] is True
    assert _state(await _job(approval_id)) == ("pending", 0, None)
    async with get_session_factory()() as session:
        pipeline = await session.get(PipelineModel, f"pipe_{seeded.context.incident_id}")
    assert pipeline is not None and pipeline.status == PipelineStatus.AWAITING_APPROVAL.value


@pytest.mark.asyncio
async def test_unauthorized_or_rejected_decisions_queue_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(approval_service, "slack_client", _Slack())
    seeded = await seed_incident("valid")
    approval_id = seeded.approval.approval_id
    assert (await _slack(approval_id, user="U_INTRUDER")).status_code == 403
    job = await _job(approval_id)
    assert (job.status, job.execution_status) == ("pending", None)

    assert (await _slack(approval_id, action="reject_fix")).status_code == 200
    job = await _job(approval_id)
    assert (job.status, job.execution_status) == ("rejected", None)


@pytest.mark.asyncio
async def test_allowlisted_slack_viewer_cannot_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 9: the global allowlist is not enough; a viewer cannot approve remediation."""
    monkeypatch.setattr(approval_service, "slack_client", _Slack())
    seeded = await seed_incident("valid")
    async with get_session_factory()() as db:
        workspace_id = (
            await db.execute(
                select(GitHubInstallationModel.workspace_id).where(
                    GitHubInstallationModel.installation_id == SEEDED_INSTALLATION_ID
                )
            )
        ).scalar_one()
        member = (
            await db.execute(
                select(WorkspaceMembershipModel.user_id).where(
                    WorkspaceMembershipModel.slack_user_id == APPROVER
                )
            )
        ).scalar_one()
        owner = (
            await db.execute(
                select(WorkspaceMembershipModel.user_id).where(
                    WorkspaceMembershipModel.workspace_id == workspace_id,
                    WorkspaceMembershipModel.role == "owner",
                )
            )
        ).scalar_one()
        await add_member(
            db, workspace_id, member, "viewer", slack_user_id=APPROVER, attested_by=owner
        )
    response = await _slack(seeded.approval.approval_id)
    assert response.status_code == 403
    job = await _job(seeded.approval.approval_id)
    assert (job.status, job.execution_status) == ("pending", None)


@pytest.mark.asyncio
async def test_slack_user_mapped_only_in_another_workspace_cannot_approve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(approval_service, "slack_client", _Slack())
    seeded = await seed_incident("valid")
    async with get_session_factory()() as db:
        await reset_identity(db)  # APPROVER now approves only for an unrelated installation
        await seed_approver_workspace(db, SEEDED_INSTALLATION_ID + 1, APPROVER)
    assert (await _slack(seeded.approval.approval_id)).status_code == 403


@pytest.mark.asyncio
async def test_attested_mapping_still_needs_the_global_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owner attestation adds to the global Slack allowlist; it never replaces it."""
    monkeypatch.setattr(approval_service, "slack_client", _Slack())
    seeded = await seed_incident("valid")
    async with get_session_factory()() as db:
        await reset_identity(db)
        await seed_approver_workspace(db, SEEDED_INSTALLATION_ID, "U0NOTLISTED")
    response = await _slack(seeded.approval.approval_id, user="U0NOTLISTED")
    assert response.status_code == 403
    job = await _job(seeded.approval.approval_id)
    assert (job.status, job.execution_status) == ("pending", None)


async def _clear_mapping(mapped_slack_user: str, **values: Any) -> None:
    async with get_session_factory()() as db:
        await db.execute(
            update(WorkspaceMembershipModel)
            .where(WorkspaceMembershipModel.slack_user_id == mapped_slack_user)
            .values(**values)
        )
        await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"slack_user_id": None, "slack_mapped_by": None, "slack_mapped_at": None},  # revoked
        {"slack_mapped_by": None, "slack_mapped_at": None},  # attestation removed (legacy)
    ],
)
async def test_job_time_authorization_refuses_a_mapping_changed_after_approval(
    monkeypatch: pytest.MonkeyPatch, change: dict[str, Any]
) -> None:
    """The decision was valid when recorded; the remediation job re-checks and refuses, before
    reading any persisted proposal, snapshot or GitHub state."""
    monkeypatch.setattr(approval_service, "slack_client", _Slack())
    seeded = await seed_incident("valid")
    approval_id = seeded.approval.approval_id
    assert (await _slack(approval_id)).status_code == 200
    assert await default_approver_authority(approval_id, APPROVER) is True
    await _clear_mapping(APPROVER, **change)
    assert await default_approver_authority(approval_id, APPROVER) is False
    unused: Any = object()  # no service may be reached
    orchestrator = RemediationOrchestrator(
        diagnostic_service=unused,
        context_resolver=unused,
        fix_service=unused,
        validation_service=unused,
        approval_service=unused,
        mutation_service=unused,
    )
    with pytest.raises(OrchestrationError, match="not an authorized approver in the workspace"):
        await orchestrator._resume_approval_unchecked(approval_id)


@pytest.mark.asyncio
async def test_nothing_runs_before_approval() -> None:
    seeded = await seed_incident("valid")
    await _only(seeded.approval.approval_id)
    orchestrator = Orchestrator()
    summary = await _stage(_worker(orchestrator))
    assert orchestrator.calls == [] and summary.counters == {}


# --- the hold ------------------------------------------------------------------------------------


def _run_requeue_tool(*approval_ids: str) -> Any:
    """The real operator CLI, in its own process, against the disposable database."""
    real_run = _REAL_RUN
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(REPO),
        "DATABASE_URL": os.environ["DATABASE_URL"],
    }
    return real_run(
        [sys.executable, "-m", "src.apps.worker.requeue_held", *approval_ids],
        cwd=REPO / "tests",  # no .env here
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.fixture
def no_side_effects(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Any subprocess or HTTP request fails the test: the hold must refuse before all of them."""
    seen: list[Any] = []

    def refuse(*args: Any, **kwargs: Any) -> Any:
        seen.append(args)
        raise AssertionError("side effect under the hold")

    async def refuse_async(*args: Any, **kwargs: Any) -> Any:
        seen.append(args)
        raise AssertionError("side effect under the hold")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse_async)
    monkeypatch.setattr(httpx.AsyncClient, "send", refuse_async)
    # Named sentinels for the three effects the hold must precede, in case one ever stops going
    # through subprocess or httpx: snapshot preparation, the executor call, and GitHub mutation.
    monkeypatch.setattr(SnapshotService, "prepare", refuse)
    monkeypatch.setattr(RemoteSandboxRunner, "run_validation", refuse_async)
    monkeypatch.setattr(GitMutationService, "create_pull_request", refuse_async)
    return seen


@pytest.mark.asyncio
async def test_the_real_hold_parks_the_job_without_side_effects_or_spent_attempts(
    no_side_effects: list[Any],
) -> None:
    approval_id = await _approve(await seed_incident("valid"))
    worker = _worker()  # the real, gated RemediationOrchestrator

    summary = await _stage(worker)
    assert summary.counters == {"remediation_held": 1}
    job = await _job(approval_id)
    assert _state(job) == ("held", 0, "remediation_hold")  # attempt refunded
    assert job.execution_next_attempt_at is None and job.status == "approved"  # decision kept
    assert await _stage(worker) == TickSummary()  # never claimed again on its own

    # Deliberate recovery for the condition-6 rollout. While the hold is active the job is just
    # held again, still without consuming an attempt.
    tool = await asyncio.to_thread(_run_requeue_tool, approval_id)
    assert tool.returncode == 0 and "requeued 1 held remediation job(s)" in tool.stdout, tool.stderr
    audit = next(
        json.loads(line)
        for line in tool.stdout.splitlines()
        if line.startswith("{") and '"remediation_requeue"' in line
    )
    assert audit["approval_ids"] == [approval_id] and audit["count"] == 1
    assert audit["requested"] == [approval_id] and audit["resulting_state"] == "pending"
    assert audit["os_uid"] == os.getuid() and audit["identity_verified"] is False
    assert audit["invoked_at"] and audit["timestamp"]
    assert _state(await _job(approval_id)) == ("pending", 0, None)
    assert (await _stage(worker)).counters == {"remediation_held": 1}
    assert _state(await _job(approval_id)) == ("held", 0, "remediation_hold")
    assert no_side_effects == []


@pytest.mark.asyncio
async def test_requeue_touches_only_approved_held_jobs() -> None:
    held = await _approve(await seed_incident("valid"))
    await _stage(_worker())
    other = await seed_incident("valid")  # pending, undecided
    async with get_session_factory()() as session:
        async with session.begin():
            assert await approval_jobs.requeue_held(session, [other.approval.approval_id]) == []
            assert await approval_jobs.requeue_held(session, [held]) == [held]


# --- outcomes after the hold (orchestrator double) -----------------------------------------------


@pytest.mark.asyncio
async def test_completed_remediation_is_recorded_once_and_never_rerun() -> None:
    approval_id = await _approve(await seed_incident("valid"))
    orchestrator = Orchestrator(PipelineStatus.COMPLETED)
    worker = _worker(orchestrator)
    assert (await _stage(worker)).counters == {"remediation_completed": 1}
    assert _state(await _job(approval_id)) == ("completed", 1, None)
    await _expire(approval_id)
    assert await _stage(worker) == TickSummary()
    assert orchestrator.calls == [approval_id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome, code",
    [
        (OrchestrationError("Approval proposal_id does not match"), "binding_mismatch"),
        (ProvenanceRejected("Snapshot is not bound", code="binding"), "provenance_binding"),
        (PipelineStatus.MUTATING, "mutation_state_unknown"),
    ],
)
async def test_terminal_outcomes_are_not_retried(outcome: Any, code: str) -> None:
    approval_id = await _approve(await seed_incident("valid"))
    orchestrator = Orchestrator(outcome)
    worker = _worker(orchestrator)
    assert (await _stage(worker)).counters == {"remediation_failed": 1}
    assert _state(await _job(approval_id)) == ("failed", 1, code)
    await _expire(approval_id)
    assert await _stage(worker) == TickSummary() and len(orchestrator.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome, code",
    [
        (PipelineStatus.FAILED, "mutation_failed"),
        (DockerUnavailableError("Validation executor unavailable"), "remediation_error"),
    ],
)
async def test_executor_or_mutation_failure_is_retried_then_exhausted(
    outcome: Any, code: str
) -> None:
    approval_id = await _approve(await seed_incident("valid"))
    orchestrator = Orchestrator(outcome)
    worker = _worker(orchestrator, max_attempts=2)

    assert (await _stage(worker)).counters == {"remediation_retry": 1}
    job = await _job(approval_id)
    assert _state(job) == ("pending", 1, code)
    assert job.execution_next_attempt_at is not None
    assert job.execution_next_attempt_at > datetime.now(UTC)  # backoff
    assert await _stage(worker) == TickSummary()  # not due yet

    await _expire(approval_id)
    assert (await _stage(worker)).counters == {"remediation_retry": 1}
    await _expire(approval_id)
    assert (await _stage(worker)).counters == {"remediation_exhausted": 1}
    assert _state(await _job(approval_id)) == ("failed", 2, "remediation_retries_exhausted")
    assert len(orchestrator.calls) == 2


# --- real authorization and binding checks -------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case, reviewer, tamper, code",
    [
        ("wrong_sha", APPROVER, None, "provenance_"),  # persisted provenance does not match
        ("valid", APPROVER, "revoke_event", "provenance_"),  # provenance revoked after approval
        ("valid", APPROVER, "rebind_pipeline", "binding_mismatch"),  # pipeline changed after
        ("valid", "U_REVOKED", "persist_proposal", "binding_mismatch"),  # approver revoked
        ("valid", APPROVER, None, "binding_mismatch"),  # the approved proposal is not persisted
    ],
)
async def test_each_attempt_reverifies_provenance_actor_and_proposal(
    case: str, reviewer: str, tamper: str | None, code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await seed_incident(case)
    if reviewer != APPROVER:
        monkeypatch.setattr(config_module.settings, "slack_approver_user_ids", [reviewer])
    approval_id = await _approve(seeded, reviewer)
    if reviewer != APPROVER:
        monkeypatch.setattr(config_module.settings, "slack_approver_user_ids", [APPROVER])
    pipeline_id = f"pipe_{seeded.context.incident_id}"
    async with get_session_factory()() as session:
        if tamper == "revoke_event":
            await session.execute(
                update(WebhookEventModel)
                .where(WebhookEventModel.pipeline_id == pipeline_id)
                .values(status="rejected")
            )
        if tamper == "rebind_pipeline":
            await session.execute(
                update(PipelineModel)
                .where(PipelineModel.pipeline_id == pipeline_id)
                .values(proposal_id="prop_other")
            )
        if tamper == "persist_proposal":  # everything else is valid: only the approver differs
            await session.execute(
                update(PipelineModel)
                .where(PipelineModel.pipeline_id == pipeline_id)
                .values(
                    proposal_json=seeded.proposal.model_dump_json(),
                    validation_json=seeded.validation.model_dump_json(),
                )
            )
        await session.commit()
    summary = await _stage(_worker(Unchecked()))
    job = await _job(approval_id)
    assert summary.counters == {"remediation_failed": 1}, (summary, _state(job))
    assert job.execution_status == "failed"
    assert (job.execution_failure_code or "").startswith(code), _state(job)


# --- claims, leases, concurrency -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_crashed_claim_is_recovered_only_after_its_lease() -> None:
    approval_id = await _approve(await seed_incident("valid"))
    async with get_session_factory()() as session:
        async with session.begin():
            claims, _ = await approval_jobs.claim_due(
                session, now=datetime.now(UTC), batch=5, lease_seconds=120, max_attempts=3
            )
    assert claims == [(approval_id, 1)]  # a worker claimed it and then crashed
    orchestrator = Orchestrator()
    worker = _worker(orchestrator)
    assert await _stage(worker) == TickSummary() and orchestrator.calls == []
    await _expire(approval_id)
    assert (await _stage(worker)).counters == {"remediation_completed": 1}
    assert _state(await _job(approval_id)) == ("completed", 2, None)  # the reclaim counted


@pytest.mark.asyncio
async def test_a_cancelled_attempt_keeps_its_claim_until_the_lease_expires() -> None:
    approval_id = await _approve(await seed_incident("valid"))
    stuck = Orchestrator(gate=asyncio.Event())
    task = asyncio.create_task(_stage(_worker(stuck)))
    await asyncio.wait_for(stuck.started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _state(await _job(approval_id)) == ("running", 1, None)
    other = Orchestrator()
    assert await _stage(_worker(other)) == TickSummary() and other.calls == []
    await _expire(approval_id)
    assert (await _stage(_worker(other))).counters == {"remediation_completed": 1}
    assert _state(await _job(approval_id)) == ("completed", 2, None)


@pytest.mark.asyncio
async def test_concurrent_workers_execute_an_approval_once() -> None:
    approval_id = await _approve(await seed_incident("valid"))
    gate = asyncio.Event()
    first = Orchestrator(gate=gate)
    running = asyncio.create_task(_stage(_worker(first)))
    await asyncio.wait_for(first.started.wait(), 5)
    others = [Orchestrator() for _ in range(3)]
    await asyncio.gather(*(_stage(_worker(o)) for o in others))
    gate.set()
    assert (await running).counters == {"remediation_completed": 1}
    assert first.calls == [approval_id] and all(o.calls == [] for o in others)
    assert _state(await _job(approval_id)) == ("completed", 1, None)


@pytest.mark.asyncio
async def test_a_busy_worker_never_holds_a_second_claim_another_worker_could_reclaim() -> None:
    # Regression: a claimed batch shared one lease while running sequentially, so a later claim
    # could expire before it started, and a second worker then ran the same approval concurrently.
    first = await _approve(await seed_incident("valid"))
    second_seed = await seed_incident("valid")
    second = second_seed.approval.approval_id
    async with get_session_factory()() as session:
        await ApprovalRepository(session).record_decision(
            second, ApprovalStatus.APPROVED, reviewer=APPROVER
        )

    gate_a, gate_b = asyncio.Event(), asyncio.Event()
    a, b = Orchestrator(gate=gate_a), Orchestrator(gate=gate_b)
    task_a = asyncio.create_task(_stage(_worker(a)))  # worker A: delayed execution
    await asyncio.wait_for(a.started.wait(), 5)
    running = a.calls[0]
    other = second if running == first else first
    assert _state(await _job(other)) == ("pending", 0, None)  # A claimed only what it is running

    # Worker B claims work: only the other approval is due, never the one A is running.
    task_b = asyncio.create_task(_stage(_worker(b)))
    await asyncio.wait_for(b.started.wait(), 5)
    assert b.calls == [other]

    gate_a.set()  # A finishes and looks for more work while B is still executing `other`
    assert (await task_a).counters == {"remediation_completed": 1}
    assert a.calls == [running]  # A never started B's approval
    gate_b.set()
    assert (await task_b).counters == {"remediation_completed": 1}
    assert b.calls == [other]
    assert _state(await _job(first)) == ("completed", 1, None)
    assert _state(await _job(second)) == ("completed", 1, None)


@pytest.mark.asyncio
async def test_a_late_result_after_lease_expiry_is_refused() -> None:
    approval_id = await _approve(await seed_incident("valid"))
    gate = asyncio.Event()
    slow = Orchestrator(PipelineStatus.FAILED, gate=gate)
    late = asyncio.create_task(_stage(_worker(slow)))
    await asyncio.wait_for(slow.started.wait(), 5)
    await _expire(approval_id)
    assert (await _stage(_worker(Orchestrator()))).counters == {"remediation_completed": 1}
    gate.set()
    assert (await late).counters == {"remediation_lease_lost": 1}
    assert _state(await _job(approval_id)) == ("completed", 2, None)  # not overwritten


@pytest.mark.asyncio
async def test_a_stale_worker_cannot_overwrite_a_newer_running_claim() -> None:
    approval_id = await _approve(await seed_incident("valid"))
    old_gate, new_gate = asyncio.Event(), asyncio.Event()
    stale = Orchestrator(PipelineStatus.COMPLETED, gate=old_gate)
    current = Orchestrator(PipelineStatus.COMPLETED, gate=new_gate)
    first = asyncio.create_task(_stage(_worker(stale)))
    await asyncio.wait_for(stale.started.wait(), 5)
    await _expire(approval_id)
    second = asyncio.create_task(_stage(_worker(current)))  # reclaims as attempt 2
    await asyncio.wait_for(current.started.wait(), 5)
    old_gate.set()
    assert (await first).counters == {"remediation_lease_lost": 1}
    assert _state(await _job(approval_id)) == ("running", 2, None)  # the newer claim is untouched
    new_gate.set()
    assert (await second).counters == {"remediation_completed": 1}
    assert _state(await _job(approval_id)) == ("completed", 2, None)


@pytest.mark.asyncio
async def test_shutdown_releases_unstarted_claims_without_spending_attempts() -> None:
    first = await _approve(await seed_incident("valid"))
    second_seed = await seed_incident("valid")
    async with get_session_factory()() as session:
        await ApprovalRepository(session).record_decision(
            second_seed.approval.approval_id, ApprovalStatus.APPROVED, reviewer=APPROVER
        )
    stop = asyncio.Event()
    orchestrator = Orchestrator()
    await _stage(_worker(orchestrator), stop, progress=stop.set)  # SIGTERM after the first job
    states = sorted(
        [_state(await _job(first)), _state(await _job(second_seed.approval.approval_id))]
    )
    assert states == [("completed", 1, None), ("pending", 0, None)]
    assert len(orchestrator.calls) == 1
