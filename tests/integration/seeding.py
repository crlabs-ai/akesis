"""Shared, disposable-database incident seeding for integration tests (Phase 1.5 / 1.6)."""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from src.packages.database.models import PipelineModel, WebhookEventModel
from src.packages.database.repositories import ApprovalRepository, PipelineRepository
from src.packages.database.session import get_session_factory
from src.packages.shared.models import (
    ApprovalRecord,
    ApprovalStatus,
    FailureCategory,
    FailureContext,
    FailureSignal,
    FixProposal,
    PipelineRecord,
    PipelineStatus,
    RepositoryIdentity,
    ValidationResult,
    ValidationStatus,
    WorkflowRunConclusion,
    WorkflowRunProvenance,
)
from src.packages.shared.persisted_provenance import semantic_key_for


@dataclass
class Seeded:
    context: FailureContext
    proposal: FixProposal
    validation: ValidationResult
    approval: ApprovalRecord


def sha_value() -> str:
    return hashlib.sha1(uuid.uuid4().bytes).hexdigest()  # noqa: S324 - random test identifier


def _provenance(
    delivery_id: str,
    run_id: int,
    sha: str,
    verified_at: datetime,
    repo_id: int = 1001,
    full_name: str = "crlabs-ai/akesis",
) -> WorkflowRunProvenance:
    repo = RepositoryIdentity(id=repo_id, full_name=full_name)
    return WorkflowRunProvenance(
        delivery_id=delivery_id,
        event_repository=repo,
        source_repository=repo,
        head_sha=sha,
        head_branch="main",
        run_id=run_id,
        run_attempt=1,
        installation_id=42,
        verified_at=verified_at,
    )


async def seed_incident(
    case: str, *, repo_id: int = 1001, full_name: str = "crlabs-ai/akesis"
) -> Seeded:
    """Persist one incident in the state a case describes, then return its in-memory objects."""
    now = datetime.now(UTC)
    uid = uuid.uuid4().hex[:12]
    incident_id = f"inc_mx_{uid}"
    run_id = int(uuid.uuid4().int % (2**40)) + 1
    sha = sha_value()
    delivery_id = f"mx-{uid}"
    verified_at = now - timedelta(hours=48) if case == "stale" else now
    provenance = _provenance(delivery_id, run_id, sha, verified_at, repo_id, full_name)

    context = FailureContext(
        incident_id=incident_id,
        provenance=provenance,
        repository_owner=full_name.split("/")[0],
        repository_name=full_name.split("/")[1],
        run_id=run_id,
        workflow_name="CI",
        commit_sha=sha,
        branch="main",
        run_url="https://example.invalid/run",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(category=FailureCategory.TEST),
    )
    if case == "missing":
        context.provenance = None
    if case == "wrong_event_id":
        # Points at the other persisted event (a different attempt), so identity must fail.
        context.provenance = provenance.model_copy(update={"delivery_id": f"{uid}-target"})
    if case == "wrong_event_repository":
        bad = RepositoryIdentity(id=9999, full_name="crlabs-ai/other")
        context.provenance = provenance.model_copy(update={"event_repository": bad})
    if case == "wrong_source_repository":
        bad = RepositoryIdentity(id=8888, full_name="crlabs-ai/akesis")
        context.provenance = provenance.model_copy(update={"source_repository": bad})
    if case == "wrong_installation":
        context.provenance = provenance.model_copy(update={"installation_id": 777})
    if case == "wrong_run":
        context.provenance = provenance.model_copy(update={"run_id": run_id + 1})
        context.run_id = run_id + 1
    if case == "wrong_attempt":
        context.provenance = provenance.model_copy(update={"run_attempt": 2})
    if case == "wrong_sha":
        other = sha_value()
        context.provenance = provenance.model_copy(update={"head_sha": other})
        context.commit_sha = other
    if case == "wrong_branch":
        context.provenance = provenance.model_copy(update={"head_branch": "other"})
        context.branch = "other"

    # Persisted state is what approval-based entry points read, so the stored context mirrors the
    # case. A record without provenance is historical (or missing); it is never actionable.
    stored_context = context.model_copy(deep=True)
    if case in {"historical", "missing"}:
        stored_context.provenance = None

    pipeline_id = f"pipe_{incident_id}"
    approval_id = f"appr_{incident_id}_{sha[:8]}"
    proposal_id = f"prop_{incident_id}"
    session_factory = get_session_factory()
    async with session_factory() as session:
        pipes = PipelineRepository(session)
        await pipes.create_pipeline(
            PipelineRecord(
                pipeline_id=pipeline_id,
                incident_id=incident_id,
                repository_owner=full_name.split("/")[0],
                repository_name=full_name.split("/")[1],
                run_id=run_id,
                commit_sha=sha,
                status=PipelineStatus.RECEIVED,
                failure_context_json=stored_context.model_dump_json(),
                created_at=now,
                updated_at=now,
            )
        )
        await pipes.update_pipeline_state(
            pipeline_id,
            PipelineStatus.AWAITING_APPROVAL,
            proposal_id=proposal_id,
            approval_id=approval_id,
        )
        if case == "pipeline_mismatch":
            await session.execute(
                update(PipelineModel)
                .where(PipelineModel.pipeline_id == pipeline_id)
                .values(incident_id=f"other_{incident_id}")
            )
            await session.commit()

        approval = ApprovalRecord(
            approval_id=approval_id,
            incident_id=incident_id,
            proposal_id=proposal_id,
            commit_sha=sha,
            status=ApprovalStatus.PENDING,
            requested_at=now,
            expires_at=now + timedelta(hours=24),
            created_at=now,
            updated_at=now,
        )
        await ApprovalRepository(session).create_approval(approval)

        if case != "unpersisted":
            status = {"rejected_event": "rejected", "unverified_event": "received"}.get(
                case, "verified"
            )
            event_run = run_id
            if case == "wrong_event_id":
                # A different, valid event for another attempt: the delivery is persisted,
                # but its identity does not match the supplied provenance.
                event_run = run_id + 10_000
            session.add(
                WebhookEventModel(
                    delivery_id=delivery_id if case != "wrong_event_id" else f"{uid}-target",
                    payload_sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                    event_type="workflow_run",
                    action="completed",
                    installation_id=42,
                    event_repository_id=repo_id,
                    event_repository_name=full_name,
                    run_id=event_run,
                    run_attempt=1,
                    source_repository_id=repo_id,
                    source_repository_name=full_name,
                    source_sha=sha,
                    source_branch="main",
                    status=status,
                    failure_code=None,
                    semantic_key=semantic_key_for(42, repo_id, event_run, 1),
                    pipeline_id=pipeline_id,
                    attempt_count=1,
                    received_at=now,
                    updated_at=now,
                    verified_at=verified_at,
                    processed_at=now,
                )
            )
            await session.commit()
        await session.commit()

    proposal = FixProposal(
        proposal_id=proposal_id,
        incident_id=incident_id,
        commit_sha=context.commit_sha,
        status="proposed",
        is_valid=True,
        unified_diff="--- a/src.py\n+++ b/src.py\n",
        target_files=["src.py"],
        rationale="fix",
        risk_level="low",
        confidence_score=0.95,
    )
    validation = ValidationResult(
        validation_id=f"val_{incident_id}",
        proposal_id=proposal_id,
        incident_id=incident_id,
        commit_sha=context.commit_sha,
        status=ValidationStatus.PASSED,
        command_executed="pytest",
        exit_code=0,
        duration_ms=1.0,
    )
    return Seeded(context=context, proposal=proposal, validation=validation, approval=approval)
