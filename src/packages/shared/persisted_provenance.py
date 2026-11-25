"""Data-dependent provenance verification against authoritative PostgreSQL state.

A FailureContext's `provenance` object is never trusted by itself. Verification loads the verified
webhook event that the signed ingestion path persisted, and compares every identity it records.
Only this module issues `VerifiedProvenance` tokens, so a token proves a successful database check.

This verification is one of two separate checks. The second is the temporary unconditional
remediation hold in `provenance.py` (ADR-0010). A valid token still does not permit execution.
"""

import hashlib
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.packages.database.models import ApprovalModel, PipelineModel, WebhookEventModel
from src.packages.database.session import get_session_factory
from src.packages.shared.models import (
    ApprovalRecord,
    FailureContext,
    FixProposal,
    ValidationResult,
    WorkflowRunProvenance,
)

# A verified event older than this is stale and must be re-delivered and re-verified.
MAX_PROVENANCE_AGE = timedelta(hours=24)
_CLOCK_SKEW = timedelta(minutes=5)


class ProvenanceRejected(Exception):
    """Persisted provenance is missing or does not match. Carries a stable, non-sensitive code."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def semantic_key_for(
    installation_id: int, event_repository_id: int, run_id: int, run_attempt: int
) -> str:
    """Stable identity of one workflow attempt, shared by ingestion and verification."""
    raw = f"{installation_id}:{event_repository_id}:{run_id}:{run_attempt}:completed"
    return hashlib.sha256(raw.encode()).hexdigest()


_ISSUE_STATE = threading.local()
_ISSUED: dict[str, tuple[Any, ...]] = {}
_MAX_ISSUED = 10_000


@dataclass(frozen=True)
class VerifiedProvenance:
    """Proof that persisted provenance matched. Constructible only through `_issue`.

    Every construction is checked against the issuance registry, including `dataclasses.replace`,
    so altering any identity field of an issued token produces a forged, rejected object.
    """

    ticket: str = field(repr=False)
    incident_id: str
    pipeline_id: str
    delivery_id: str
    owner: str
    name: str
    head_sha: str
    head_branch: str
    run_id: int
    run_attempt: int
    installation_id: int
    event_repository_id: int
    source_repository_id: int
    verified_at: datetime
    semantic_key: str

    def __post_init__(self) -> None:
        if getattr(_ISSUE_STATE, "active", False):
            return
        if _ISSUED.get(self.ticket) != self._values():
            raise ProvenanceRejected(
                "Provenance tokens are issued only by verification", code="forged"
            )

    def _values(self) -> tuple[Any, ...]:
        return tuple(getattr(self, item.name) for item in fields(self) if item.name != "ticket")


def _issue(**values: Any) -> VerifiedProvenance:
    """The only constructor path for a token. Called only after a successful database check."""
    ticket = uuid.uuid4().hex
    _ISSUE_STATE.active = True
    try:
        token = VerifiedProvenance(ticket=ticket, **values)
    finally:
        _ISSUE_STATE.active = False
    _ISSUED[ticket] = token._values()
    while len(_ISSUED) > _MAX_ISSUED:
        _ISSUED.pop(next(iter(_ISSUED)))
    return token


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def require_bound_token(
    token: object,
    *,
    incident_id: str | None = None,
    owner: str,
    name: str,
    head_sha: str,
) -> VerifiedProvenance:
    """Sync-boundary check: a token must exist and be bound to the caller's exact arguments."""
    if not isinstance(token, VerifiedProvenance):
        raise ProvenanceRejected("Verified provenance is required", code="missing")
    if (
        (incident_id is not None and token.incident_id != incident_id)
        or token.owner != owner
        or token.name != name
        or token.head_sha != head_sha
    ):
        raise ProvenanceRejected(
            "Provenance token does not match the requested work", code="binding"
        )
    return token


def bind_work_to_context(
    context: FailureContext,
    proposal: FixProposal | None = None,
    validation: ValidationResult | None = None,
    approval: ApprovalRecord | None = None,
) -> None:
    """Every derived artefact must describe the same incident and commit as the verified context."""
    for item in (proposal, validation, approval):
        if item is None:
            continue
        if item.incident_id != context.incident_id or item.commit_sha != context.commit_sha:
            raise ProvenanceRejected("Artefact does not match verified context", code="binding")


async def _verify(
    session: AsyncSession, context: FailureContext, pipeline_id_hint: str | None = None
) -> VerifiedProvenance:
    provenance = context.provenance
    if provenance is None:
        raise ProvenanceRejected("Provenance is missing", code="missing")
    if not isinstance(provenance, WorkflowRunProvenance):
        raise ProvenanceRejected("Provenance is malformed", code="malformed")

    source_full = f"{context.repository_owner}/{context.repository_name}"
    if (
        provenance.head_sha != context.commit_sha
        or provenance.head_branch != context.branch
        or provenance.run_id != context.run_id
        or provenance.source_repository.full_name != source_full
    ):
        raise ProvenanceRejected("Context does not match its provenance", code="context_mismatch")

    # Wrong event ID: the delivery must exist as a persisted record.
    event = (
        await session.execute(
            select(WebhookEventModel).where(WebhookEventModel.delivery_id == provenance.delivery_id)
        )
    ).scalar_one_or_none()
    if event is None:
        raise ProvenanceRejected(
            "Provenance is not backed by a persisted event", code="unpersisted"
        )

    if event.status == "rejected":
        raise ProvenanceRejected("Persisted event was rejected", code="rejected")
    if event.status != "verified":
        raise ProvenanceRejected("Persisted event is not verified", code="unverified")
    if event.failure_code is not None:
        raise ProvenanceRejected("Persisted event carries a failure code", code="unverified")
    if event.event_type != "workflow_run" or event.action != "completed":
        raise ProvenanceRejected("Persisted event is not a completed workflow run", code="action")

    expected_semantic = semantic_key_for(
        provenance.installation_id,
        provenance.event_repository.id,
        provenance.run_id,
        provenance.run_attempt,
    )
    if event.semantic_key != expected_semantic:
        raise ProvenanceRejected("Workflow attempt identity does not match", code="identity")

    checks = {
        "installation": (event.installation_id, provenance.installation_id),
        "event repository id": (event.event_repository_id, provenance.event_repository.id),
        "event repository name": (
            event.event_repository_name,
            provenance.event_repository.full_name,
        ),
        "source repository id": (event.source_repository_id, provenance.source_repository.id),
        "source repository name": (
            event.source_repository_name,
            provenance.source_repository.full_name,
        ),
        "workflow run": (event.run_id, provenance.run_id),
        "run attempt": (event.run_attempt, provenance.run_attempt),
        "source SHA": (event.source_sha, provenance.head_sha),
        "source branch": (event.source_branch, provenance.head_branch),
    }
    for label, (persisted, supplied) in checks.items():
        if persisted != supplied:
            raise ProvenanceRejected(f"Persisted {label} does not match", code="mismatch")

    if event.verified_at is None or _utc(event.verified_at) != _utc(provenance.verified_at):
        raise ProvenanceRejected("Verified timestamp does not match", code="stale")
    now = datetime.now(UTC)
    verified_at = _utc(event.verified_at)
    if verified_at > now + _CLOCK_SKEW or now - verified_at > MAX_PROVENANCE_AGE:
        raise ProvenanceRejected("Verified provenance is stale", code="stale")

    # Pipeline and incident identity: the event must be bound to this incident's pipeline.
    if event.pipeline_id is None:
        raise ProvenanceRejected("Event is not bound to a pipeline", code="unbound")
    if pipeline_id_hint is not None and event.pipeline_id != pipeline_id_hint:
        raise ProvenanceRejected("Event is bound to another pipeline", code="pipeline_mismatch")
    pipeline = (
        await session.execute(
            select(PipelineModel).where(PipelineModel.pipeline_id == event.pipeline_id)
        )
    ).scalar_one_or_none()
    if pipeline is None:
        raise ProvenanceRejected("Bound pipeline does not exist", code="unbound")
    if (
        pipeline.incident_id != context.incident_id
        or pipeline.run_id != context.run_id
        or pipeline.commit_sha != context.commit_sha
        or pipeline.repository_owner != context.repository_owner
        or pipeline.repository_name != context.repository_name
    ):
        raise ProvenanceRejected("Pipeline does not match the incident", code="pipeline_mismatch")

    # The persisted pipeline context must carry the same provenance. Historical records without
    # it are non-actionable unless the controlled recovery service has bound them.
    stored_context = (
        FailureContext.model_validate_json(pipeline.failure_context_json)
        if pipeline.failure_context_json
        else None
    )
    if stored_context is None or stored_context.provenance is None:
        raise ProvenanceRejected("Historical record lacks verified provenance", code="historical")
    if stored_context.provenance != provenance:
        raise ProvenanceRejected(
            "Stored provenance differs from supplied provenance", code="mismatch"
        )

    return _issue(
        incident_id=context.incident_id,
        pipeline_id=pipeline.pipeline_id,
        delivery_id=provenance.delivery_id,
        owner=context.repository_owner,
        name=context.repository_name,
        head_sha=provenance.head_sha,
        head_branch=provenance.head_branch,
        run_id=provenance.run_id,
        run_attempt=provenance.run_attempt,
        installation_id=provenance.installation_id,
        event_repository_id=provenance.event_repository.id,
        source_repository_id=provenance.source_repository.id,
        verified_at=_utc(provenance.verified_at),
        semantic_key=expected_semantic,
    )


async def _run_with_session(
    operation: Callable[[AsyncSession], Any], session: AsyncSession | None
) -> Any:
    """Fail closed: any database failure becomes a rejection, never a pass."""
    try:
        if session is not None:
            return await operation(session)
        async with get_session_factory()() as own:
            return await operation(own)
    except ProvenanceRejected:
        raise
    except Exception:
        raise ProvenanceRejected(
            "Provenance could not be verified from persisted state", code="unavailable"
        ) from None


async def verify_context_provenance(
    context: FailureContext, *, session: AsyncSession | None = None
) -> VerifiedProvenance:
    """Verify a context's provenance against persisted, linked, verified webhook state."""
    return cast(
        VerifiedProvenance,
        await _run_with_session(lambda s: _verify(s, context), session),
    )


async def verify_approval_provenance(
    approval_id: str, *, session: AsyncSession | None = None
) -> VerifiedProvenance:
    """Resolve an approval to its pipeline and verify the context persisted for that pipeline."""

    async def operation(db: AsyncSession) -> VerifiedProvenance:
        approval = (
            await db.execute(select(ApprovalModel).where(ApprovalModel.approval_id == approval_id))
        ).scalar_one_or_none()
        if approval is None:
            raise ProvenanceRejected("Approval is not persisted", code="unpersisted")
        pipeline = (
            await db.execute(select(PipelineModel).where(PipelineModel.approval_id == approval_id))
        ).scalar_one_or_none()
        if pipeline is None or not pipeline.failure_context_json:
            raise ProvenanceRejected("Approval is not bound to a pipeline", code="unbound")
        if pipeline.incident_id != approval.incident_id:
            raise ProvenanceRejected(
                "Approval does not match the incident", code="pipeline_mismatch"
            )
        context = FailureContext.model_validate_json(pipeline.failure_context_json)
        if context.provenance is None:
            raise ProvenanceRejected(
                "Historical record lacks verified provenance", code="historical"
            )
        return await _verify(db, context, pipeline_id_hint=pipeline.pipeline_id)

    return cast(VerifiedProvenance, await _run_with_session(operation, session))
