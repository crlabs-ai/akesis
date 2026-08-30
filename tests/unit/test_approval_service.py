from datetime import UTC, datetime, timedelta

import pytest

from src.packages.shared.approval_service import (
    ApprovalNotFoundError,
    ApprovalService,
    IllegalStateTransitionError,
    IneligibleApprovalError,
)
from src.packages.shared.models import (
    ApprovalRecord,
    ApprovalStatus,
    FailureCategory,
    FailureContext,
    FailureSignal,
    FixProposal,
    ValidationResult,
    ValidationStatus,
    WorkflowRunConclusion,
)


class MockSlackClient:
    """Mock Slack client recording posted and updated cards."""

    def __init__(self) -> None:
        self.posted_cards: list[ApprovalRecord] = []
        self.updated_cards: list[ApprovalRecord] = []

    async def post_approval_card(
        self,
        approval: ApprovalRecord,
        context: FailureContext,
        proposal: FixProposal,
        validation: ValidationResult,
    ) -> dict[str, str]:
        self.posted_cards.append(approval)
        return {"channel_id": "C12345", "message_ts": "1234567890.123"}

    async def update_approval_card(
        self,
        response_url: str,
        approval: ApprovalRecord,
    ) -> bool:
        self.updated_cards.append(approval)
        return True


@pytest.fixture
def sample_data() -> tuple[FailureContext, FixProposal, ValidationResult]:
    context = FailureContext(
        incident_id="inc_appr_01",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=101,
        workflow_name="CI",
        commit_sha="112233445566",
        branch="main",
        run_url="https://ci",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=FailureCategory.TEST,
            error_type="ZeroDivisionError",
            message="division by zero",
            target_file="src/calc.py",
            target_line=1,
            extracted_snippet="err",
        ),
        raw_log_excerpt="err",
    )

    proposal = FixProposal(
        proposal_id="prop_01",
        incident_id="inc_appr_01",
        commit_sha="112233445566",
        status="proposed",
        is_valid=True,
        unified_diff="--- a/src/calc.py\n+++ b/src/calc.py\n@@ -1,1 +1,2 @@\n+guard\n",
        target_files=["src/calc.py"],
        rationale="Add guard",
        assumptions=[],
        risk_level="low",
        has_dependency_changes=False,
        confidence_score=0.95,
    )

    validation = ValidationResult(
        validation_id="val_01",
        proposal_id="prop_01",
        incident_id="inc_appr_01",
        commit_sha="112233445566",
        status=ValidationStatus.PASSED,
        command_executed="pytest",
        exit_code=0,
        stdout="1 passed",
        stderr="",
        duration_ms=45.0,
        timed_out=False,
    )
    return context, proposal, validation


@pytest.mark.asyncio
async def test_request_approval_success(
    sample_data: tuple[FailureContext, FixProposal, ValidationResult],
) -> None:
    context, proposal, validation = sample_data
    mock_slack = MockSlackClient()
    service = ApprovalService(slack_client=mock_slack)

    record = await service.request_approval(context, proposal, validation)
    assert record.status == ApprovalStatus.PENDING
    assert record.slack_channel_id == "C12345"
    assert record.slack_message_ts == "1234567890.123"
    assert len(mock_slack.posted_cards) == 1


@pytest.mark.asyncio
async def test_request_approval_ineligible_on_validation_failure(
    sample_data: tuple[FailureContext, FixProposal, ValidationResult],
) -> None:
    context, proposal, validation = sample_data
    validation.status = ValidationStatus.FAILED
    validation.exit_code = 1

    service = ApprovalService()
    with pytest.raises(IneligibleApprovalError):
        await service.request_approval(context, proposal, validation)


@pytest.mark.asyncio
async def test_request_approval_ineligible_on_low_confidence(
    sample_data: tuple[FailureContext, FixProposal, ValidationResult],
) -> None:
    context, proposal, validation = sample_data
    proposal.confidence_score = 0.40

    service = ApprovalService()
    with pytest.raises(IneligibleApprovalError):
        await service.request_approval(context, proposal, validation)


@pytest.mark.asyncio
async def test_record_decision_approve_and_reject(
    sample_data: tuple[FailureContext, FixProposal, ValidationResult],
) -> None:
    context, proposal, validation = sample_data
    mock_slack = MockSlackClient()
    service = ApprovalService(slack_client=mock_slack)

    record = await service.request_approval(context, proposal, validation)

    # Approve
    updated, is_dup = await service.record_decision(
        approval_id=record.approval_id,
        decision="approve",
        decided_by="lead_dev",
        response_url="https://hooks.slack.com/actions/123",
    )
    assert updated.status == ApprovalStatus.APPROVED
    assert updated.decided_by == "lead_dev"
    assert is_dup is False
    assert len(mock_slack.updated_cards) == 1


@pytest.mark.asyncio
async def test_record_decision_idempotent_duplicate(
    sample_data: tuple[FailureContext, FixProposal, ValidationResult],
) -> None:
    context, proposal, validation = sample_data
    service = ApprovalService(slack_client=MockSlackClient())
    record = await service.request_approval(context, proposal, validation)

    # First approve
    await service.record_decision(record.approval_id, "approve", "dev1")

    # Second approve (duplicate)
    updated, is_dup = await service.record_decision(record.approval_id, "approve", "dev1")
    assert updated.status == ApprovalStatus.APPROVED
    assert is_dup is True


@pytest.mark.asyncio
async def test_record_decision_illegal_transition_rejected(
    sample_data: tuple[FailureContext, FixProposal, ValidationResult],
) -> None:
    context, proposal, validation = sample_data
    service = ApprovalService(slack_client=MockSlackClient())
    record = await service.request_approval(context, proposal, validation)

    # Approve
    await service.record_decision(record.approval_id, "approve", "dev1")

    # Attempt Reject after Approve
    with pytest.raises(IllegalStateTransitionError):
        await service.record_decision(record.approval_id, "reject", "dev2")


@pytest.mark.asyncio
async def test_record_decision_not_found() -> None:
    service = ApprovalService()
    with pytest.raises(ApprovalNotFoundError):
        await service.record_decision("non_existent", "approve", "dev")


@pytest.mark.asyncio
async def test_record_decision_expired(
    sample_data: tuple[FailureContext, FixProposal, ValidationResult],
) -> None:
    context, proposal, validation = sample_data
    service = ApprovalService(slack_client=MockSlackClient())
    record = await service.request_approval(context, proposal, validation)

    # Manually expire the record
    record.expires_at = datetime.now(UTC) - timedelta(hours=1)

    with pytest.raises(IllegalStateTransitionError):
        await service.record_decision(record.approval_id, "approve", "dev")
