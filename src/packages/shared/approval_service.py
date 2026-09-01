from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta

from src.packages.database.repositories import (
    ApprovalRepository,
    ApprovalRepositoryProtocol,
)
from src.packages.database.session import get_session_factory
from src.packages.sdk.slack_client import SlackClient, SlackClientProtocol
from src.packages.shared.config import settings
from src.packages.shared.logging import get_logger
from src.packages.shared.models import (
    ApprovalRecord,
    ApprovalStatus,
    FailureContext,
    FixProposal,
    ValidationResult,
    ValidationStatus,
)

logger = get_logger("akesis.approval_service")


class ApprovalError(Exception):
    """Base exception for approval workflow."""

    pass


class IneligibleApprovalError(ApprovalError):
    """Raised when a proposal is not eligible for human approval."""

    pass


class IllegalStateTransitionError(ApprovalError):
    """Raised when an illegal approval state transition is attempted."""

    pass


class ApprovalNotFoundError(ApprovalError):
    """Raised when an approval record cannot be located."""

    pass


@asynccontextmanager
async def default_repository_factory() -> AsyncIterator[ApprovalRepositoryProtocol]:
    """Default repository factory creating an async database session."""
    factory = get_session_factory()
    async with factory() as session:
        yield ApprovalRepository(session)


class ApprovalService:
    """Orchestrates approval gate lifecycle, eligibility, and atomic state transitions."""

    def __init__(
        self,
        repository_factory: (
            Callable[[], AbstractAsyncContextManager[ApprovalRepositoryProtocol]] | None
        ) = None,
        slack_client: SlackClientProtocol | None = None,
        ttl_hours: int | None = None,
    ) -> None:
        self.repository_factory = repository_factory or default_repository_factory
        self.slack_client = slack_client or SlackClient()
        self.ttl_hours = ttl_hours or settings.approval_ttl_hours

    def check_eligibility(
        self,
        proposal: FixProposal,
        validation: ValidationResult,
    ) -> tuple[bool, str | None]:
        """Evaluates whether a proposal satisfies strict deterministic criteria for approval."""
        if not proposal.is_valid or proposal.status != "proposed" or not proposal.unified_diff:
            return False, "FixProposal is invalid, rejected, or empty."

        if validation.status != ValidationStatus.PASSED or validation.exit_code != 0:
            return False, f"Sandbox validation did not pass (status: {validation.status})."

        if proposal.confidence_score < settings.min_fix_confidence_threshold:
            return (
                False,
                f"Confidence {proposal.confidence_score:.2f} below threshold "
                f"{settings.min_fix_confidence_threshold:.2f}.",
            )

        return True, None

    async def request_approval(
        self,
        context: FailureContext,
        proposal: FixProposal,
        validation: ValidationResult,
    ) -> ApprovalRecord:
        """Creates a durable PostgreSQL approval record and posts card to Slack if eligible."""
        is_eligible, reason = self.check_eligibility(proposal, validation)
        if not is_eligible:
            logger.info(
                "approval_request_ineligible",
                proposal_id=proposal.proposal_id,
                reason=reason,
            )
            raise IneligibleApprovalError(reason or "Proposal is ineligible for human review.")

        approval_id = f"appr_{context.incident_id}_{proposal.commit_sha[:8]}"
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=self.ttl_hours)

        record = ApprovalRecord(
            approval_id=approval_id,
            incident_id=context.incident_id,
            proposal_id=proposal.proposal_id,
            commit_sha=proposal.commit_sha,
            status=ApprovalStatus.PENDING,
            requested_at=now,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )

        # 1. Persist authoritative record in database
        async with self.repository_factory() as repo:
            persisted_record = await repo.create_approval(record)

        # 2. Post interactive Block Kit card to Slack
        try:
            slack_meta = await self.slack_client.post_approval_card(
                approval=persisted_record,
                context=context,
                proposal=proposal,
                validation=validation,
            )
            persisted_record.slack_channel_id = slack_meta.get("channel_id")
            persisted_record.slack_message_ts = slack_meta.get("message_ts")
        except Exception as err:
            logger.warning(
                "slack_card_post_error",
                approval_id=approval_id,
                error=str(err),
            )

        logger.info(
            "approval_requested",
            approval_id=approval_id,
            status=persisted_record.status.value,
        )
        return persisted_record

    async def record_decision(
        self,
        approval_id: str,
        decision: str,
        decided_by: str,
        decision_reason: str | None = None,
        response_url: str | None = None,
    ) -> tuple[ApprovalRecord, bool]:
        """Atomically transitions approval record status in database and updates Slack card."""
        target_status = (
            ApprovalStatus.APPROVED if decision == "approve" else ApprovalStatus.REJECTED
        )

        async with self.repository_factory() as repo:
            # 1. Fetch current record to check existence and expiration
            current = await repo.get_approval(approval_id)
            if not current:
                raise ApprovalNotFoundError(f"Approval record '{approval_id}' not found.")

            now = datetime.now(UTC)
            is_expired = current.expires_at and now > current.expires_at
            if is_expired and current.status == ApprovalStatus.PENDING:
                await repo.expire_approval(approval_id)
                raise IllegalStateTransitionError(
                    f"Approval '{approval_id}' has expired and cannot be decided."
                )

            # 2. Atomic database state transition
            record, is_duplicate = await repo.record_decision(
                approval_id=approval_id,
                target_status=target_status,
                reviewer=decided_by,
                reason=decision_reason,
            )

            if record is None:
                raise ApprovalNotFoundError(f"Approval record '{approval_id}' not found.")

            # If not duplicate and record status is not target_status -> illegal transition
            if not is_duplicate and record.status != target_status:
                logger.warning(
                    "approval_illegal_transition_attempted",
                    approval_id=approval_id,
                    current_status=record.status.value,
                    attempted_status=target_status.value,
                )
                raise IllegalStateTransitionError(
                    f"Cannot transition '{record.status.value}' to '{target_status.value}'"
                )

        logger.info(
            "approval_decision_recorded",
            approval_id=approval_id,
            status=record.status.value,
            decided_by=decided_by,
            is_duplicate=is_duplicate,
        )

        # 3. Update Slack card via response_url if provided
        if response_url:
            try:
                await self.slack_client.update_approval_card(response_url, record)
            except Exception as err:
                logger.warning(
                    "slack_card_update_error",
                    approval_id=approval_id,
                    error=str(err),
                )

        return record, is_duplicate

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        """Retrieves an approval record by ID from database."""
        async with self.repository_factory() as repo:
            return await repo.get_approval(approval_id)


# Global approval service singleton for API usage
approval_service = ApprovalService()
