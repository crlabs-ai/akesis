import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import pytest
from httpx import ASGITransport, AsyncClient

from src.apps.api.main import create_app
from src.packages.database.repositories import PipelineRepository
from src.packages.database.session import get_session_factory
from src.packages.shared.approval_service import approval_service
from src.packages.shared.config import Settings, settings
from src.packages.shared.models import (
    FailureCategory,
    FailureContext,
    FailureSignal,
    FixProposal,
    PipelineRecord,
    PipelineStatus,
    ValidationResult,
    ValidationStatus,
    WorkflowRunConclusion,
)
from tests.support.tenancy import reset_identity, seed_approver_workspace

FLOW_INSTALLATION_ID = 4343


@pytest.fixture
def app() -> Any:
    return create_app()


@pytest.fixture
def approver_app() -> Any:
    """Explicit Slack policy: default-deny applies to the shared `app` fixture."""
    return create_app(
        Settings(
            environment="test",
            slack_approver_user_ids=["U12345"],
            slack_workspace_id="T1",
            slack_channel_id="C1",
        )
    )


class _StubSlackClient:
    """Controlled Slack double: no network calls from tests."""

    async def post_approval_card(
        self, approval: Any, context: Any, proposal: Any, validation: Any
    ) -> dict[str, str]:
        return {"channel_id": "C1", "message_ts": "1700000000.000100"}

    async def update_approval_card(self, response_url: str, approval: Any) -> bool:
        return True


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
async def test_slack_interaction_approve_endpoint(
    approver_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    now_ts = int(time.time())
    context = FailureContext(
        incident_id=f"inc_flow_{now_ts}",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=202,
        workflow_name="CI",
        commit_sha=f"aabbcc{now_ts}"[:12],
        branch="main",
        run_url="https://ci",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(category=FailureCategory.TEST, message="failed"),
        raw_log_excerpt="err",
    )
    proposal = FixProposal(
        proposal_id=f"prop_flow_{now_ts}",
        incident_id=context.incident_id,
        commit_sha=context.commit_sha,
        status="proposed",
        is_valid=True,
        unified_diff="--- a/a.py\n+++ b/a.py\n",
        target_files=["a.py"],
        rationale="Fix a",
        risk_level="low",
        confidence_score=0.90,
    )
    validation = ValidationResult(
        validation_id=f"val_flow_{now_ts}",
        proposal_id=proposal.proposal_id,
        incident_id=context.incident_id,
        commit_sha=context.commit_sha,
        status=ValidationStatus.PASSED,
        command_executed="pytest",
        exit_code=0,
        duration_ms=40.0,
    )

    monkeypatch.setattr(approval_service, "slack_client", _StubSlackClient())
    record = await approval_service._request_approval_unchecked(context, proposal, validation)

    # A decision is only accepted for a pipeline awaiting approval for this exact proposal/commit.
    pipeline_id = f"pipe_{context.incident_id}"
    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        pipes = PipelineRepository(session)
        await pipes.create_pipeline(
            PipelineRecord(
                pipeline_id=pipeline_id,
                incident_id=context.incident_id,
                repository_owner=context.repository_owner,
                repository_name=context.repository_name,
                run_id=context.run_id,
                commit_sha=context.commit_sha,
                installation_id=FLOW_INSTALLATION_ID,
                status=PipelineStatus.RECEIVED,
                created_at=now,
                updated_at=now,
            )
        )
        await reset_identity(session)
        await seed_approver_workspace(session, FLOW_INSTALLATION_ID, "U12345")
        await pipes.update_pipeline_state(
            pipeline_id,
            PipelineStatus.AWAITING_APPROVAL,
            proposal_id=proposal.proposal_id,
            approval_id=record.approval_id,
        )

    payload_dict = {
        "actions": [{"action_id": "approve_fix", "value": record.approval_id}],
        "user": {"username": "lead_engineer", "id": "U12345"},
        "team": {"id": "T1"},
        "channel": {"id": "C1"},
        "response_url": "https://hooks.slack.com/actions/test",
    }
    body_str = urlencode({"payload": json.dumps(payload_dict)})
    body_bytes = body_str.encode("utf-8")
    timestamp = str(int(time.time()))
    sig = create_slack_signature(body_bytes, timestamp, settings.slack_signing_secret)

    transport = ASGITransport(app=approver_app)
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
