"""Controlled recovery of historical pipelines into verified provenance.

This is the only path, besides signed ingestion, that may add provenance to a persisted pipeline.
It never infers repository IDs, installation context or commit identity. A historical record is
recoverable only when all of the following hold:

- Its pipeline has a parseable context whose identity matches the pipeline row.
- Exactly one verified webhook event is bound to that pipeline, with every identity field present.
- The event's semantic key matches the identity it records.
- A fresh installation-scoped GitHub verification of that event succeeds.

On success the recovered provenance and the event's verified timestamp are written in the same
transaction, so the persisted verifier later accepts the record. Anything else returns False and
the record stays non-actionable.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.packages.database.models import PipelineModel, WebhookEventModel
from src.packages.shared.models import FailureContext
from src.packages.shared.persisted_provenance import semantic_key_for
from src.packages.shared.workflow_provenance import (
    ProvenanceVerificationError,
    WorkflowRunProvenanceVerifier,
)


async def recover_historical_provenance(
    session: AsyncSession, pipeline_id: str, verifier: WorkflowRunProvenanceVerifier
) -> bool:
    """Recover one eligible historical pipeline. The caller owns the transaction and commits."""
    pipeline = (
        await session.execute(
            select(PipelineModel).where(PipelineModel.pipeline_id == pipeline_id).with_for_update()
        )
    ).scalar_one_or_none()
    if pipeline is None or not pipeline.failure_context_json:
        return False
    try:
        context = FailureContext.model_validate_json(pipeline.failure_context_json)
    except ValueError:
        return False
    if (
        context.incident_id != pipeline.incident_id
        or context.run_id != pipeline.run_id
        or context.commit_sha != pipeline.commit_sha
        or context.repository_owner != pipeline.repository_owner
        or context.repository_name != pipeline.repository_name
    ):
        return False

    events = list(
        (
            await session.execute(
                select(WebhookEventModel).where(
                    WebhookEventModel.pipeline_id == pipeline_id,
                    WebhookEventModel.status == "verified",
                )
            )
        ).scalars()
    )
    if len(events) != 1:
        return False  # zero or ambiguous bindings are never recoverable
    event = events[0]
    if event.action != "completed" or event.event_type != "workflow_run":
        return False
    if (
        event.installation_id is None
        or event.event_repository_id is None
        or event.event_repository_name is None
        or event.source_repository_id is None
        or event.source_repository_name is None
        or event.source_sha is None
        or event.source_branch is None
        or event.run_id is None
        or event.run_attempt is None
        or event.semantic_key is None
    ):
        return False
    if event.semantic_key != semantic_key_for(
        event.installation_id,
        event.event_repository_id,
        event.run_id,
        event.run_attempt,
    ):
        return False

    if context.provenance is not None:
        existing = context.provenance
        return (
            existing.delivery_id == event.delivery_id
            and existing.installation_id == event.installation_id
            and existing.event_repository.id == event.event_repository_id
            and existing.event_repository.full_name == event.event_repository_name
            and existing.source_repository.id == event.source_repository_id
            and existing.source_repository.full_name == event.source_repository_name
            and existing.run_id == event.run_id == context.run_id
            and existing.run_attempt == event.run_attempt
            and existing.head_sha == event.source_sha == context.commit_sha
            and existing.head_branch == event.source_branch == context.branch
        )

    payload = {
        "action": event.action,
        "installation": {"id": event.installation_id},
        "repository": {"id": event.event_repository_id, "full_name": event.event_repository_name},
        "workflow_run": {
            "id": event.run_id,
            "run_attempt": event.run_attempt,
            "head_repository": {
                "id": event.source_repository_id,
                "full_name": event.source_repository_name,
            },
            "head_sha": event.source_sha,
            "head_branch": event.source_branch,
        },
    }
    try:
        provenance = await verifier.verify(payload, event.delivery_id)
    except ProvenanceVerificationError:
        return False
    if (
        provenance.run_id != context.run_id
        or provenance.head_sha != context.commit_sha
        or provenance.head_branch != context.branch
        or f"{context.repository_owner}/{context.repository_name}"
        != provenance.source_repository.full_name
    ):
        return False

    context.provenance = provenance
    pipeline.failure_context_json = context.model_dump_json()
    event.verified_at = provenance.verified_at
    await session.flush()
    return True
