"""Durable remediation execution claims on approvals (Phase 1.14c). Mirrors `log_jobs`.

States: pending -> running -> completed | failed, and held. While running, execution_next_attempt_at
is the claim lease: a crashed worker's claim is reclaimed after it expires, and that counts as an
attempt. The worker claims one job at a time, immediately before executing it, so a lease never
has to cover time spent queued behind another job. Every write after a claim is guarded by
(status, attempt), so a superseded worker cannot overwrite a newer outcome, and no worker ever
changes a claim that another worker owns.

`held`: the remediation hold refused the attempt. The attempt is refunded and the row is never
claimed again until an operator runs `requeue_held` (the condition-6 rollout).
"""

from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.packages.database.models import ApprovalModel

PENDING, RUNNING, COMPLETED, FAILED, HELD = "pending", "running", "completed", "failed", "held"
RECORDED, LEASE_LOST = "recorded", "lease_lost"
HOLD_CODE = "remediation_hold"


async def claim_due(
    session: AsyncSession, *, now: datetime, batch: int, lease_seconds: int, max_attempts: int
) -> tuple[list[tuple[str, int]], int]:
    """Claim due approved jobs. Returns (claims as (approval_id, attempt), number exhausted)."""
    rows = (
        await session.execute(
            select(ApprovalModel.approval_id, ApprovalModel.execution_attempts)
            .where(
                ApprovalModel.status == "approved",
                ApprovalModel.execution_status.in_((PENDING, RUNNING)),
                or_(
                    ApprovalModel.execution_next_attempt_at.is_(None),
                    ApprovalModel.execution_next_attempt_at <= now,
                ),
            )
            .order_by(ApprovalModel.decided_at)
            .limit(batch)
            .with_for_update(skip_locked=True)
        )
    ).all()
    claims: list[tuple[str, int]] = []
    exhausted = 0
    for approval_id, attempts in rows:
        if attempts >= max_attempts:
            await session.execute(
                update(ApprovalModel)
                .where(ApprovalModel.approval_id == approval_id)
                .values(
                    execution_status=FAILED,
                    execution_next_attempt_at=None,
                    execution_failure_code="remediation_retries_exhausted",
                )
            )
            exhausted += 1
            continue
        await session.execute(
            update(ApprovalModel)
            .where(ApprovalModel.approval_id == approval_id)
            .values(
                execution_status=RUNNING,
                execution_attempts=attempts + 1,
                execution_next_attempt_at=now + timedelta(seconds=lease_seconds),
            )
        )
        claims.append((approval_id, attempts + 1))
    return claims, exhausted


def _claim(approval_id: str, attempt: int) -> list[ColumnElement[bool]]:
    return [
        ApprovalModel.approval_id == approval_id,
        ApprovalModel.execution_status == RUNNING,
        ApprovalModel.execution_attempts == attempt,
    ]


async def complete(
    session: AsyncSession,
    approval_id: str,
    attempt: int,
    *,
    status: str,
    code: str | None = None,
    next_attempt_at: datetime | None = None,
) -> str:
    """Record one claimed attempt's outcome: RECORDED, or LEASE_LOST if the claim is not ours."""
    result = await session.execute(
        update(ApprovalModel)
        .where(*_claim(approval_id, attempt))
        .values(
            execution_status=status,
            execution_failure_code=code,
            execution_next_attempt_at=next_attempt_at,
        )
    )
    return RECORDED if getattr(result, "rowcount", 0) else LEASE_LOST


async def hold(session: AsyncSession, approval_id: str, attempt: int) -> str:
    """The hold refused this attempt: park the job and refund the attempt (nothing ran)."""
    result = await session.execute(
        update(ApprovalModel)
        .where(*_claim(approval_id, attempt))
        .values(
            execution_status=HELD,
            execution_attempts=attempt - 1,
            execution_next_attempt_at=None,
            execution_failure_code=HOLD_CODE,
        )
    )
    return RECORDED if getattr(result, "rowcount", 0) else LEASE_LOST


async def requeue_held(session: AsyncSession, approval_ids: list[str] | None = None) -> list[str]:
    """Operator action for the condition-6 rollout: move held jobs back to pending.

    Only rows that are still approved and held are touched; returns exactly those approval IDs.
    While the hold is active, a requeued job is simply held again on its next claim, without
    consuming an attempt.
    """
    guards = [ApprovalModel.status == "approved", ApprovalModel.execution_status == HELD]
    if approval_ids is not None:
        guards.append(ApprovalModel.approval_id.in_(approval_ids))
    result = await session.execute(
        update(ApprovalModel)
        .where(*guards)
        .values(
            execution_status=PENDING,
            execution_next_attempt_at=None,
            execution_failure_code=None,
        )
        .returning(ApprovalModel.approval_id)
    )
    return sorted(result.scalars().all())
