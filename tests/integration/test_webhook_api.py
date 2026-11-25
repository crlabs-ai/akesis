import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from src.apps.api import routes
from src.apps.api.main import create_app
from src.packages.shared.config import Settings, settings
from src.packages.shared.models import RepositoryIdentity, WorkflowRunProvenance

# Ephemeral test-only App key; never used for real GitHub calls (verification is stubbed below).
_TEST_APP_KEY = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    .decode()
)
# App configured so ingestion reaches provenance verification rather than the not-configured 503.
client = TestClient(
    create_app(
        Settings(environment="test", github_app_id=123, github_app_private_key=_TEST_APP_KEY)
    )
)
FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__),
    "../fixtures/webhook_workflow_run_failed.json",
)
LOG_FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__),
    "../fixtures/logs_pytest_failure.txt",
)


def compute_signature(payload_bytes: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _delivery_id() -> str:
    """Unique per request so reruns against the persistent disposable database never conflict."""
    return f"test-delivery-{uuid.uuid4().hex}"


def _verified_shaped_payload() -> bytes:
    """Fixture delivery plus the identity fields that signed provenance requires.

    Each call gets a fresh run ID: attempts are idempotent per run, and the disposable database
    persists across test sessions, so a fixed ID would make reruns report "already processed".
    """
    with open(FIXTURE_PATH, "rb") as f:
        data = json.loads(f.read())
    data["workflow_run"]["id"] = uuid.uuid4().int % (2**40)
    repo = {
        "id": 1001,
        "name": "akesis",
        "full_name": "crlabs-ai/akesis",
        "owner": {"login": "crlabs-ai"},
    }
    data["repository"] = repo
    data["installation"] = {"id": 42}
    data["workflow_run"]["run_attempt"] = 1
    data["workflow_run"]["head_repository"] = dict(repo)
    return json.dumps(data).encode("utf-8")


@pytest.fixture
def stub_provenance_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Controlled dependency: verifier GitHub behaviour is covered by test_workflow_provenance."""

    class _StubVerifier:
        def __init__(self, app_auth: Any, api_client: Any) -> None:
            pass

        async def verify(self, payload: dict[str, Any], delivery_id: str) -> WorkflowRunProvenance:
            run = payload["workflow_run"]
            repo = payload["repository"]
            source = run["head_repository"]
            return WorkflowRunProvenance(
                delivery_id=delivery_id,
                event_repository=RepositoryIdentity(id=repo["id"], full_name=repo["full_name"]),
                source_repository=RepositoryIdentity(
                    id=source["id"], full_name=source["full_name"]
                ),
                head_sha=run["head_sha"],
                head_branch=run["head_branch"],
                run_id=run["id"],
                run_attempt=run["run_attempt"],
                installation_id=payload["installation"]["id"],
                verified_at=datetime.now(UTC),
            )

    monkeypatch.setattr(routes, "WorkflowRunProvenanceVerifier", _StubVerifier)


def test_health_endpoints() -> None:
    live = client.get("/health/liveness")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}

    ready = client.get("/health/readiness")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


def test_webhook_unauthorized_missing_signature() -> None:
    response = client.post(
        "/v1/webhooks/github",
        json={"action": "completed"},
        headers={"X-GitHub-Event": "workflow_run"},
    )
    assert response.status_code == 401


def test_webhook_unsupported_event() -> None:
    payload = b'{"action": "opened"}'
    sig = compute_signature(payload, settings.github_webhook_secret)
    response = client.post(
        "/v1/webhooks/github",
        content=payload,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": sig,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code in (200, 202)
    assert response.json()["status"] == "ignored"


def test_webhook_malformed_json() -> None:
    payload = b"not-valid-json{"
    sig = compute_signature(payload, settings.github_webhook_secret)
    response = client.post(
        "/v1/webhooks/github",
        content=payload,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": sig,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400


def test_webhook_missing_required_objects() -> None:
    payload = b'{"action": "completed"}'
    sig = compute_signature(payload, settings.github_webhook_secret)
    response = client.post(
        "/v1/webhooks/github",
        content=payload,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": sig,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 422


def test_webhook_non_failure_conclusion_ignored() -> None:
    payload = (
        b'{"action": "completed", "workflow_run": {"id": 1, "conclusion": "success"}, '
        b'"repository": {"name": "akesis", "owner": {"login": "crlabs"}}}'
    )
    sig = compute_signature(payload, settings.github_webhook_secret)
    response = client.post(
        "/v1/webhooks/github",
        content=payload,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": sig,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code in (200, 202)
    assert response.json()["status"] == "ignored"
    assert "not a failure" in response.json()["message"]


def test_webhook_successful_ingestion_and_diagnosis(stub_provenance_verifier: None) -> None:
    payload_bytes = _verified_shaped_payload()

    with open(LOG_FIXTURE_PATH) as f:
        mock_logs = f.read()

    sig = compute_signature(payload_bytes, settings.github_webhook_secret)

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/987654321/logs").respond(
            status_code=200,
            text=mock_logs,
        )

        response = client.post(
            "/v1/webhooks/github",
            content=payload_bytes,
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-GitHub-Delivery": _delivery_id(),
                "X-Hub-Signature-256": sig,
                "Content-Type": "application/json",
            },
        )

        # Provenance is verified inline; remediation stays disabled behind the hold (ADR-0010).
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["incident_id"] is not None
        assert data["message"] == "Provenance verified; remediation remains disabled"


def test_webhook_logs_not_found_handled_safely(stub_provenance_verifier: None) -> None:
    payload_bytes = _verified_shaped_payload()

    sig = compute_signature(payload_bytes, settings.github_webhook_secret)

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/987654321/logs").respond(
            status_code=404,
            text="Logs not found",
        )

        response = client.post(
            "/v1/webhooks/github",
            content=payload_bytes,
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-GitHub-Delivery": _delivery_id(),
                "X-Hub-Signature-256": sig,
                "Content-Type": "application/json",
            },
        )

        # Log retrieval now runs behind the hold; ingestion acceptance must not depend on it.
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["message"] == "Provenance verified; remediation remains disabled"


def test_webhook_api_error_handled_safely(stub_provenance_verifier: None) -> None:
    payload_bytes = _verified_shaped_payload()

    sig = compute_signature(payload_bytes, settings.github_webhook_secret)

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/987654321/logs").respond(
            status_code=500,
            text="Server error",
        )

        response = client.post(
            "/v1/webhooks/github",
            content=payload_bytes,
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-GitHub-Delivery": _delivery_id(),
                "X-Hub-Signature-256": sig,
                "Content-Type": "application/json",
            },
        )

        # Log retrieval now runs behind the hold; ingestion acceptance must not depend on it.
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["message"] == "Provenance verified; remediation remains disabled"


def test_openapi_endpoint() -> None:
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    assert "paths" in data
    assert "/health/liveness" in data["paths"]
    assert "/health/readiness" in data["paths"]
    assert "/v1/webhooks/github" in data["paths"]
    assert "/v1/slack/interactions" in data["paths"]


def test_slack_interactions_missing_signature() -> None:
    resp = client.post(
        "/v1/slack/interactions",
        data={"payload": "{}"},
    )
    assert resp.status_code == 401


def test_slack_interactions_invalid_signature() -> None:
    resp = client.post(
        "/v1/slack/interactions",
        data={"payload": "{}"},
        headers={
            "X-Slack-Request-Timestamp": "1234567890",
            "X-Slack-Signature": "v0=invalid",
        },
    )
    assert resp.status_code == 401
