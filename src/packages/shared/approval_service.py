import asyncio
from datetime import UTC, datetime, timedelta

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


class ApprovalService:
    """Orchestrates approval gate lifecycle, eligibility, and atomic state transitions."""

    def __init__(
        self,
        slack_client: SlackClientProtocol | None = None,
        ttl_hours: int | None = None,
    ) -> None:
        self.slack_client = slack_client or SlackClient()
        self.ttl_hours = ttl_hours or settings.approval_ttl_hours
        self._records: dict[str, ApprovalRecord] = {}
        self._lock = asyncio.Lock()

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
        """Creates an approval record and posts interactive card to Slack if eligible."""
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

        async with self._lock:
            self._records[approval_id] = record

        # Post interactive Block Kit card to Slack
        try:
            slack_meta = await self.slack_client.post_approval_card(
                approval=record,
                context=context,
                proposal=proposal,
                validation=validation,
            )
            async with self._lock:
                record.slack_channel_id = slack_meta.get("channel_id")
                record.slack_message_ts = slack_meta.get("message_ts")
                record.updated_at = datetime.now(UTC)
        except Exception as err:
            logger.warning("slack_card_post_error", approval_id=approval_id, error=str(err))

        logger.info("approval_requested", approval_id=approval_id, status=record.status)
        return record

    async def record_decision(
        self,
        approval_id: str,
        decision: str,
        decided_by: str,
        decision_reason: str | None = None,
        response_url: str | None = None,
    ) -> tuple[ApprovalRecord, bool]:
        """Atomically transitions approval record status and updates Slack card."""
        target_status = (
            ApprovalStatus.APPROVED if decision == "approve" else ApprovalStatus.REJECTED
        )

        async with self._lock:
            record = self._records.get(approval_id)
            if not record:
                raise ApprovalNotFoundError(f"Approval record '{approval_id}' not found.")

            # Check expiration
            now = datetime.now(UTC)
            is_expired = record.expires_at and now > record.expires_at
            if is_expired and record.status == ApprovalStatus.PENDING:
                record.status = ApprovalStatus.EXPIRED
                record.updated_at = now
                raise IllegalStateTransitionError(
                    f"Approval '{approval_id}' has expired and cannot be decided."
                )

            # Idempotency check
            if record.status == target_status:
                logger.info(
                    "approval_decision_idempotent_duplicate",
                    approval_id=approval_id,
                    status=record.status,
                )
                return record, True

            # Reject conflicting transitions from terminal states
            if record.status != ApprovalStatus.PENDING:
                logger.warning(
                    "approval_illegal_transition_attempted",
                    approval_id=approval_id,
                    current_status=record.status,
                    attempted_status=target_status,
                )
                raise IllegalStateTransitionError(
                    f"Cannot transition from terminal state '{record.status}' to '{target_status}'"
                )

            # Atomic State Transition
            record.status = target_status
            record.decided_at = now
            record.decided_by = decided_by
            record.decision_reason = decision_reason
            record.updated_at = now

        logger.info(
            "approval_decision_recorded",
            approval_id=approval_id,
            status=record.status,
            decided_by=decided_by,
        )

        # Update Slack card if response_url is present
        if response_url:
            try:
                await self.slack_client.update_approval_card(response_url, record)
            except Exception as err:
                logger.warning("slack_card_update_error", approval_id=approval_id, error=str(err))

        return record, False

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        """Retrieves an approval record by ID."""
        async with self._lock:
            return self._records.get(approval_id)


# Global approval service singleton for API usage
approval_service = ApprovalService()
