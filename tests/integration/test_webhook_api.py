import hashlib
import hmac
import os

import respx
from fastapi.testclient import TestClient

from src.apps.api.main import app
from src.packages.shared.config import settings

client = TestClient(app)
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
    assert response.status_code == 200
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
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert "not a failure" in response.json()["message"]


def test_webhook_successful_ingestion_and_diagnosis() -> None:
    with open(FIXTURE_PATH, "rb") as f:
        payload_bytes = f.read()

    with open(LOG_FIXTURE_PATH) as f:
        mock_logs = f.read()

    sig = compute_signature(payload_bytes, settings.github_webhook_secret)

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/987654321/logs").respond(
            status_code=200,
            text=mock_logs,
        )

        response = client.post(
            "/v1/webhooks/github",
            content=payload_bytes,
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": sig,
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["incident_id"] is not None
        assert data["category"] == "test"
        assert "ZeroDivisionError" in data["message"]


def test_webhook_logs_not_found_handled_safely() -> None:
    with open(FIXTURE_PATH, "rb") as f:
        payload_bytes = f.read()

    sig = compute_signature(payload_bytes, settings.github_webhook_secret)

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/987654321/logs").respond(
            status_code=404,
            text="Logs not found",
        )

        response = client.post(
            "/v1/webhooks/github",
            content=payload_bytes,
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": sig,
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["category"] == "unknown"


def test_webhook_api_error_handled_safely() -> None:
    with open(FIXTURE_PATH, "rb") as f:
        payload_bytes = f.read()

    sig = compute_signature(payload_bytes, settings.github_webhook_secret)

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/987654321/logs").respond(
            status_code=500,
            text="Server error",
        )

        response = client.post(
            "/v1/webhooks/github",
            content=payload_bytes,
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": sig,
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["category"] == "unknown"
