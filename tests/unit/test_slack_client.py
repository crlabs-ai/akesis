import time

from src.packages.sdk.slack_client import SlackClient, verify_slack_signature
from src.packages.shared.models import (
    ApprovalRecord,
    FailureCategory,
    FailureContext,
    FailureSignal,
    FixProposal,
    ValidationResult,
    ValidationStatus,
    WorkflowRunConclusion,
)


def test_verify_slack_signature_valid() -> None:
    secret = "secret123"
    timestamp = str(int(time.time()))
    body = b"payload=test_body"

    import hashlib
    import hmac

    sig_base = f"v0:{timestamp}:payload=test_body"
    signature = (
        "v0="
        + hmac.new(
            key=secret.encode("utf-8"),
            msg=sig_base.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()
    )

    assert verify_slack_signature(body, timestamp, signature, secret) is True


def test_verify_slack_signature_invalid() -> None:
    secret = "secret123"
    timestamp = str(int(time.time()))
    body = b"payload=test_body"
    assert verify_slack_signature(body, timestamp, "v0=invalid_sig", secret) is False


def test_verify_slack_signature_expired_timestamp() -> None:
    secret = "secret123"
    # 10 minutes ago
    timestamp = str(int(time.time()) - 600)
    body = b"payload=test_body"
    assert verify_slack_signature(body, timestamp, "v0=sig", secret) is False


def test_build_approval_blocks() -> None:
    client = SlackClient()
    approval = ApprovalRecord(
        approval_id="appr_01",
        incident_id="inc_01",
        proposal_id="prop_01",
        commit_sha="abcdef123456",
    )
    context = FailureContext(
        incident_id="inc_01",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=1,
        workflow_name="CI",
        commit_sha="abcdef123456",
        branch="main",
        run_url="https://ci",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(category=FailureCategory.TEST, message="test failed"),
        raw_log_excerpt="err",
    )
    proposal = FixProposal(
        proposal_id="prop_01",
        incident_id="inc_01",
        commit_sha="abcdef123456",
        status="proposed",
        is_valid=True,
        unified_diff="--- a/test.py\n+++ b/test.py\n",
        target_files=["test.py"],
        rationale="Fix test",
        risk_level="low",
        confidence_score=0.95,
    )
    validation = ValidationResult(
        validation_id="val_01",
        proposal_id="prop_01",
        incident_id="inc_01",
        commit_sha="abcdef123456",
        status=ValidationStatus.PASSED,
        command_executed="pytest",
        exit_code=0,
        duration_ms=50.0,
    )

    blocks = client._build_approval_blocks(approval, context, proposal, validation)
    assert len(blocks) == 6
    assert any("CI REMEDIATION APPROVAL REQUEST" in str(b) for b in blocks)
    assert any("approve_fix" in str(b) for b in blocks)
