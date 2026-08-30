import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

import pytest
from httpx import ASGITransport, AsyncClient

from src.apps.api.main import create_app
from src.packages.shared.approval_service import approval_service
from src.packages.shared.config import settings
from src.packages.shared.models import (
    FailureCategory,
    FailureContext,
    FailureSignal,
    FixProposal,
    ValidationResult,
    ValidationStatus,
    WorkflowRunConclusion,
)


@pytest.fixture
def app() -> Any:
    return create_app()


def create_slack_signature(body: bytes, timestamp: str, secret: str) -> str:
    sig_base = f"v0:{timestamp}:{body.decode('utf-8')}"
    return (
        "v0="
        + hmac.new(
            key=secret.encode("utf-8"),
            msg=sig_base.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()
    )


@pytest.mark.asyncio
async def test_slack_interaction_approve_endpoint(app: Any) -> None:
    # Setup pending approval record
    context = FailureContext(
        incident_id="inc_api_01",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=202,
        workflow_name="CI",
        commit_sha="aabbccddeeff",
        branch="main",
        run_url="https://ci",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(category=FailureCategory.TEST, message="failed"),
        raw_log_excerpt="err",
    )
    proposal = FixProposal(
        proposal_id="prop_api_01",
        incident_id="inc_api_01",
        commit_sha="aabbccddeeff",
        status="proposed",
        is_valid=True,
        unified_diff="--- a/a.py\n+++ b/a.py\n",
        target_files=["a.py"],
        rationale="Fix a",
        risk_level="low",
        confidence_score=0.90,
    )
    validation = ValidationResult(
        validation_id="val_api_01",
        proposal_id="prop_api_01",
        incident_id="inc_api_01",
        commit_sha="aabbccddeeff",
        status=ValidationStatus.PASSED,
        command_executed="pytest",
        exit_code=0,
        duration_ms=40.0,
    )

    record = await approval_service.request_approval(context, proposal, validation)

    payload_dict = {
        "actions": [{"action_id": "approve_fix", "value": record.approval_id}],
        "user": {"username": "lead_engineer", "id": "U12345"},
        "response_url": "https://hooks.slack.com/actions/test",
    }
    body_str = urlencode({"payload": json.dumps(payload_dict)})
    body_bytes = body_str.encode("utf-8")
    timestamp = str(int(time.time()))
    sig = create_slack_signature(body_bytes, timestamp, settings.slack_signing_secret)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/v1/slack/interactions",
            content=body_bytes,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Signature": sig,
                "X-Slack-Request-Timestamp": timestamp,
            },
        )

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["decision"] == "approved"
        assert not data["is_duplicate"]


@pytest.mark.asyncio
async def test_slack_interaction_unauthorized_signature(app: Any) -> None:
    body_bytes = b"payload=%7B%7D"
    timestamp = str(int(time.time()))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/v1/slack/interactions",
            content=body_bytes,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Signature": "v0=forged_signature",
                "X-Slack-Request-Timestamp": timestamp,
            },
        )
        assert res.status_code == 401
