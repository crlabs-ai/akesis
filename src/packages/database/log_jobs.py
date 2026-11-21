"""Durable claims for workflow-log retrieval on pipelines (Phase 1.10).

States: pending -> fetching -> extracted | unavailable. While fetching, ``log_next_attempt_at`` is
the claim lease: a crashed worker's claim is reclaimed after it expires, and that counts as an
attempt. Every completion is guarded by (status, attempt), so a superseded worker cannot overwrite a
newer outcome.
"""

from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.packages.database.models import PipelineModel

PENDING, FETCHING, EXTRACTED, UNAVAILABLE = "pending", "fetching", "extracted", "unavailable"


async def claim_due(
    session: AsyncSession, *, now: datetime, batch: int, lease_seconds: int, max_attempts: int
) -> tuple[list[tuple[str, int]], int]:
    """Claim due pipelines. Returns (claims as (pipeline_id, attempt), number exhausted)."""
    rows = (
        await session.execute(
            select(PipelineModel.pipeline_id, PipelineModel.log_attempts)
            .where(
                PipelineModel.log_status.in_((PENDING, FETCHING)),
                or_(
                    PipelineModel.log_next_attempt_at.is_(None),
                    PipelineModel.log_next_attempt_at <= now,
                ),
            )
            .order_by(PipelineModel.created_at)
            .limit(batch)
            .with_for_update(skip_locked=True)
        )
    ).all()
    claims: list[tuple[str, int]] = []
    exhausted = 0
    for pipeline_id, attempts in rows:
        if attempts >= max_attempts:
            await session.execute(
                update(PipelineModel)
                .where(PipelineModel.pipeline_id == pipeline_id)
                .values(
                    log_status=UNAVAILABLE,
                    log_next_attempt_at=None,
                    log_failure_code="log_retrieval_exhausted",
                )
            )
            exhausted += 1
            continue
        await session.execute(
            update(PipelineModel)
            .where(PipelineModel.pipeline_id == pipeline_id)
            .values(
                log_status=FETCHING,
                log_attempts=attempts + 1,
                log_next_attempt_at=now + timedelta(seconds=lease_seconds),
            )
        )
        claims.append((pipeline_id, attempts + 1))
    return claims, exhausted


async def complete(
    session: AsyncSession,
    pipeline_id: str,
    attempt: int,
    *,
    status: str,
    code: str | None = None,
    failure_context_json: str | None = None,
    next_attempt_at: datetime | None = None,
    expected_context_json: str | None = None,
) -> bool:
    """Record one claimed attempt's outcome. False if the claim was superseded.

    A new context is written only if the stored context is still the one the worker read, so a
    concurrent change to the incident is never overwritten; the lease then expires and the
    attempt is retried against the current row.
    """
    values: dict[str, object] = {
        "log_status": status,
        "log_failure_code": code,
        "log_next_attempt_at": next_attempt_at,
    }
    if failure_context_json is not None:
        values["failure_context_json"] = failure_context_json
    guards = [
        PipelineModel.pipeline_id == pipeline_id,
        PipelineModel.log_status == FETCHING,
        PipelineModel.log_attempts == attempt,
    ]
    if failure_context_json is not None:
        guards.append(PipelineModel.failure_context_json == expected_context_json)
    result = await session.execute(update(PipelineModel).where(*guards).values(**values))
    return bool(getattr(result, "rowcount", 0))


async def release_unstarted(session: AsyncSession, pipeline_id: str, attempt: int) -> bool:
    """Return a claim that was never started, without consuming its attempt."""
    result = await session.execute(
        update(PipelineModel)
        .where(
            PipelineModel.pipeline_id == pipeline_id,
            PipelineModel.log_status == FETCHING,
            PipelineModel.log_attempts == attempt,
        )
        .values(log_status=PENDING, log_attempts=attempt - 1, log_next_attempt_at=None)
    )
    return bool(getattr(result, "rowcount", 0))
