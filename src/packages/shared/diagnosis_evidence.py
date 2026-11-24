"""The only source of failure evidence for diagnosis (Phase 1.11).

Diagnosis must see the signal the log worker persisted, not whatever a caller holds: provenance
verification compares provenance only, so a stale (empty) or forged signal would otherwise pass.
``load_diagnosis_context`` returns the persisted context after checking that it is the same incident
and re-verifying provenance against PostgreSQL. Pending or in-flight log retrieval is a typed,
retry-safe refusal; it is bounded because the log worker always ends in ``extracted`` or
``unavailable`` (attempts are capped). ``unavailable`` keeps the empty signal and sets
``log_evidence`` (Phase 1.12), so the prompt says that logs are missing and why, instead of
presenting an empty signal as if the logs held no failure.
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.packages.database import log_jobs
from src.packages.database.models import PipelineModel
from src.packages.shared.models import FailureContext, LogEvidence
from src.packages.shared.persisted_provenance import ProvenanceRejected, verify_context_provenance


class LogEvidencePending(Exception):
    """Workflow logs are not final yet. No diagnosis, no side effect; try again later."""


async def load_diagnosis_context(
    context: FailureContext, *, session: AsyncSession
) -> FailureContext:
    pipeline = (
        await session.execute(
            select(PipelineModel).where(PipelineModel.incident_id == context.incident_id)
        )
    ).scalar_one_or_none()
    if pipeline is None or not pipeline.failure_context_json:
        raise ProvenanceRejected("Incident has no persisted context", code="unbound")
    if pipeline.log_status in (log_jobs.PENDING, log_jobs.FETCHING):
        raise LogEvidencePending(context.incident_id)
    try:
        stored = FailureContext.model_validate_json(pipeline.failure_context_json)
    except ValueError:
        raise ProvenanceRejected("Persisted context is malformed", code="malformed") from None
    if (
        stored.incident_id != context.incident_id
        or stored.commit_sha != context.commit_sha
        or stored.repository_owner != context.repository_owner
        or stored.repository_name != context.repository_name
        or stored.run_id != context.run_id
        or stored.provenance != context.provenance
        or pipeline.commit_sha != stored.commit_sha
        or pipeline.run_id != stored.run_id
    ):
        raise ProvenanceRejected("Persisted evidence does not match this incident", code="binding")
    await verify_context_provenance(stored, session=session)
    if pipeline.log_status == log_jobs.EXTRACTED:
        evidence = LogEvidence(status="retrieved")
    else:
        code = pipeline.log_failure_code or "unknown"
        evidence = LogEvidence(
            status="unavailable",
            reason_code=code if re.fullmatch(r"[a-z0-9_]{1,64}", code) else "unknown",
        )
    return stored.model_copy(update={"log_evidence": evidence})
