from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.packages.database.models import ApprovalModel
from src.packages.shared.logging import get_logger
from src.packages.shared.models import ApprovalRecord, ApprovalStatus

logger = get_logger("akesis.database.repository")


class ApprovalRepositoryProtocol(Protocol):
    """Protocol defining persistence operations for human approvals."""

    async def create_approval(self, record: ApprovalRecord) -> ApprovalRecord:
        """Persists a new pending approval record."""
        ...

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        """Retrieves an approval record by ID."""
        ...

    async def get_by_proposal_id(self, proposal_id: str) -> ApprovalRecord | None:
        """Retrieves an approval record by fix proposal ID."""
        ...

    async def record_decision(
        self,
        approval_id: str,
        target_status: ApprovalStatus,
        reviewer: str,
        reason: str | None = None,
    ) -> tuple[ApprovalRecord | None, bool]:
        """Atomically transitions approval status if pending, returning (record, is_duplicate)."""
        ...

    async def expire_approval(self, approval_id: str) -> bool:
        """Transitions a pending approval to expired status."""
        ...


class ApprovalRepository:
    """SQLAlchemy async implementation of ApprovalRepositoryProtocol."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_domain(self, model: ApprovalModel) -> ApprovalRecord:
        """Maps ORM model to Pydantic domain model."""
        return ApprovalRecord(
            approval_id=model.approval_id,
            incident_id=model.incident_id,
            diagnosis_id=model.diagnosis_id,
            proposal_id=model.proposal_id,
            commit_sha=model.commit_sha,
            status=ApprovalStatus(model.status),
            slack_channel_id=model.slack_channel_id,
            slack_message_ts=model.slack_message_ts,
            requested_at=model.requested_at,
            decided_at=model.decided_at,
            decided_by=model.reviewer,
            decision_reason=model.reason,
            expires_at=model.expires_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def create_approval(self, record: ApprovalRecord) -> ApprovalRecord:
        """Persists a new approval record in PostgreSQL."""
        model = ApprovalModel(
            approval_id=record.approval_id,
            incident_id=record.incident_id,
            diagnosis_id=record.diagnosis_id,
            proposal_id=record.proposal_id,
            commit_sha=record.commit_sha,
            status=record.status.value,
            slack_channel_id=record.slack_channel_id,
            slack_message_ts=record.slack_message_ts,
            reviewer=record.decided_by,
            reason=record.decision_reason,
            requested_at=record.requested_at,
            expires_at=record.expires_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        logger.info("approval_record_persisted", approval_id=record.approval_id)
        return self._to_domain(model)

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        """Loads approval record from PostgreSQL by approval_id."""
        stmt = select(ApprovalModel).where(ApprovalModel.approval_id == approval_id)
        res = await self.session.execute(stmt)
        model = res.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_by_proposal_id(self, proposal_id: str) -> ApprovalRecord | None:
        """Loads approval record from PostgreSQL by proposal_id."""
        stmt = select(ApprovalModel).where(ApprovalModel.proposal_id == proposal_id)
        res = await self.session.execute(stmt)
        model = res.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def record_decision(
        self,
        approval_id: str,
        target_status: ApprovalStatus,
        reviewer: str,
        reason: str | None = None,
    ) -> tuple[ApprovalRecord | None, bool]:
        """Executes an atomic conditional update at the database level."""
        now = datetime.now(UTC)

        # 1. Atomic conditional update WHERE status = 'pending'
        stmt = (
            update(ApprovalModel)
            .where(
                ApprovalModel.approval_id == approval_id,
                ApprovalModel.status == ApprovalStatus.PENDING.value,
            )
            .values(
                status=target_status.value,
                reviewer=reviewer,
                reason=reason,
                decided_at=now,
                updated_at=now,
            )
            .returning(ApprovalModel)
        )
        res = await self.session.execute(stmt)
        updated_model = res.scalar_one_or_none()

        if updated_model is not None:
            await self.session.commit()
            logger.info(
                "approval_decision_committed",
                approval_id=approval_id,
                status=target_status.value,
                reviewer=reviewer,
            )
            return self._to_domain(updated_model), False

        # 2. Row was not updated -> Inspect current record to distinguish duplicate vs conflict
        current_record = await self.get_approval(approval_id)
        if current_record is None:
            return None, False

        # If already transitioned to the target status -> idempotent duplicate
        if current_record.status == target_status:
            logger.info(
                "approval_decision_idempotent_duplicate",
                approval_id=approval_id,
                status=current_record.status.value,
            )
            return current_record, True

        # Current record is in a conflicting terminal state
        return current_record, False

    async def expire_approval(self, approval_id: str) -> bool:
        """Transitions a pending approval to expired status."""
        now = datetime.now(UTC)
        stmt = (
            update(ApprovalModel)
            .where(
                ApprovalModel.approval_id == approval_id,
                ApprovalModel.status == ApprovalStatus.PENDING.value,
            )
            .values(
                status=ApprovalStatus.EXPIRED.value,
                updated_at=now,
            )
            .returning(ApprovalModel.approval_id)
        )
        res = await self.session.execute(stmt)
        updated_id = res.scalar_one_or_none()
        await self.session.commit()
        return updated_id is not None
