import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from src.apps.api.main import create_app
from src.packages.shared.approval_service import approval_service
from src.packages.shared.config import Settings, settings
from src.packages.shared.models import ApprovalRecord, ApprovalStatus


@pytest.mark.parametrize(
    ("user", "team", "channel", "action", "expected"),
    [
        ("U1", "T1", "C1", "approve_fix", 200),
        ("U2", "T1", "C1", "approve_fix", 403),
        ("U1", "T2", "C1", "approve_fix", 403),
        ("U1", "T1", "C2", "approve_fix", 403),
        ("U1", "T1", "C1", "other", 400),
    ],
)
def test_signed_slack_policy(
    monkeypatch: pytest.MonkeyPatch, user: str, team: str, channel: str, action: str, expected: int
) -> None:
    policy = Settings(
        environment="test",
        slack_approver_user_ids=["U1"],
        slack_workspace_id="T1",
        slack_channel_id="C1",
    )
    app = create_app(policy)
    record = ApprovalRecord(
        approval_id="a",
        incident_id="i",
        proposal_id="p",
        commit_sha="a" * 40,
        status=ApprovalStatus.APPROVED,
    )
    mock = AsyncMock(return_value=(record, True))
    monkeypatch.setattr(approval_service, "record_decision", mock)
    payload = {
        "user": {"id": user},
        "team": {"id": team},
        "channel": {"id": channel},
        "actions": [{"action_id": action, "value": "a"}],
    }
    body = urlencode({"payload": json.dumps(payload)}).encode()
    timestamp = str(int(time.time()))
    signature = (
        "v0="
        + hmac.new(
            settings.slack_signing_secret.encode(),
            b"v0:" + timestamp.encode() + b":" + body,
            hashlib.sha256,
        ).hexdigest()
    )
    response = TestClient(app).post(
        "/v1/slack/interactions",
        content=body,
        headers={"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": signature},
    )
    assert response.status_code == expected
    if expected == 200:
        mock.assert_awaited_once()
        assert mock.await_args is not None
        assert mock.await_args.kwargs["decided_by"] == "U1"
    else:
        mock.assert_not_awaited()
