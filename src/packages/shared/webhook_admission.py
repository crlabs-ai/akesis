"""Admit a delivery whose workflow-run provenance has been verified.

This is the single post-verification path shared by the webhook route and the retry worker. It
never runs remediation. It persists a pipeline record marked failed with the remediation-disabled
reason, then marks the delivery verified. The temporary remediation hold (ADR-0010) therefore
holds for both entry points by construction, not by a check in either caller.

Every write is guarded by the current attempt (``status == verifying`` and matching
``attempt_count``), so a superseded attempt cannot write over a newer one.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.packages.database.models import PipelineModel, WebhookEventModel
from src.packages.shared.models import (
    FailureContext,
    FailureSignal,
    PipelineRecord,
    PipelineStatus,
    WorkflowRunConclusion,
    WorkflowRunProvenance,
)
from src.packages.shared.persisted_provenance import semantic_key_for

REMEDIATION_DISABLED_REASON = "Remediation disabled pending verified sandbox implementation"

AdmissionResult = Literal["verified", "duplicate", "conflict", "superseded"]


@dataclass(frozen=True)
class AdmissionOutcome:
    result: AdmissionResult
    incident_id: str | None = None


async def admit_verified_delivery(
    session: AsyncSession,
    *,
    delivery_id: str,
    attempt_number: int,
    provenance: WorkflowRunProvenance,
    workflow_name: str,
    run_url: str,
    conclusion: WorkflowRunConclusion,
) -> AdmissionOutcome:
    """Persist a verified delivery exactly once. The caller must have verified provenance."""
    incident_id = f"inc_{uuid.uuid4().hex[:12]}"
    source_owner, source_name = provenance.source_repository.full_name.split("/", 1)
    context = FailureContext(
        incident_id=incident_id,
        provenance=provenance,
        repository_owner=source_owner,
        repository_name=source_name,
        run_id=provenance.run_id,
        workflow_name=workflow_name,
        commit_sha=provenance.head_sha,
        branch=provenance.head_branch,
        run_url=run_url,
        conclusion=conclusion,
        signal=FailureSignal(),
    )
    semantic = semantic_key_for(
        provenance.installation_id,
        provenance.event_repository.id,
        provenance.run_id,
        provenance.run_attempt,
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": semantic}
    )
    current = (
        await session.execute(
            select(WebhookEventModel)
            .where(WebhookEventModel.delivery_id == delivery_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if current.status != "verifying" or current.attempt_count != attempt_number:
        await session.rollback()
        return AdmissionOutcome("superseded")
    prior = (
        await session.execute(
            select(WebhookEventModel).where(WebhookEventModel.semantic_key == semantic)
        )
    ).scalar_one_or_none()
    if prior is not None:
        if (
            prior.source_repository_id != provenance.source_repository.id
            or prior.source_repository_name != provenance.source_repository.full_name
            or prior.source_sha != provenance.head_sha
            or prior.source_branch != provenance.head_branch
        ):
            await session.execute(
                update(WebhookEventModel)
                .where(
                    WebhookEventModel.delivery_id == delivery_id,
                    WebhookEventModel.status == "verifying",
                    WebhookEventModel.attempt_count == attempt_number,
                )
                .values(
                    status="rejected",
                    failure_code="semantic_identity_conflict",
                    last_error_code="semantic_identity_conflict",
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
            return AdmissionOutcome("conflict")
        await session.execute(
            update(WebhookEventModel)
            .where(
                WebhookEventModel.delivery_id == delivery_id,
                WebhookEventModel.status == "verifying",
                WebhookEventModel.attempt_count == attempt_number,
            )
            .values(
                status="duplicate",
                pipeline_id=prior.pipeline_id,
                updated_at=datetime.now(UTC),
                processed_at=datetime.now(UTC),
            )
        )
        await session.commit()
        return AdmissionOutcome("duplicate")
    record = PipelineRecord(
        pipeline_id=f"pipe_{incident_id}",
        incident_id=incident_id,
        repository_owner=source_owner,
        repository_name=source_name,
        run_id=provenance.run_id,
        commit_sha=provenance.head_sha,
        installation_id=provenance.installation_id,
        status=PipelineStatus.FAILED,
        failure_reason=REMEDIATION_DISABLED_REASON,
        failure_context_json=context.model_dump_json(),
    )
    session.add(PipelineModel(**record.model_dump(exclude={"status"}), status=record.status.value))
    await session.flush()
    finished = await session.execute(
        update(WebhookEventModel)
        .where(
            WebhookEventModel.delivery_id == delivery_id,
            WebhookEventModel.status == "verifying",
            WebhookEventModel.attempt_count == attempt_number,
        )
        .values(
            status="verified",
            action="completed",
            semantic_key=semantic,
            pipeline_id=record.pipeline_id,
            verified_at=provenance.verified_at,
            processed_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            failure_code=None,
            last_error_code=None,
        )
        .returning(WebhookEventModel.delivery_id)
    )
    if finished.scalar_one_or_none() is None:
        await session.rollback()
        return AdmissionOutcome("superseded")
    await session.commit()
    return AdmissionOutcome("verified", incident_id)
