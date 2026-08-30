#!/usr/bin/env python3
import os
import sys

# Ensure repository root is on sys.path for standalone script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import hashlib
import hmac
import json

import respx
from fastapi.testclient import TestClient

from src.apps.api.main import app
from src.packages.shared.config import settings


def run_smoke_test() -> None:
    print("=== Akesis Phase 1: Ingestion & Log Signal Extraction Smoke Test ===")
    client = TestClient(app)

    # 1. Health check
    print("[1/5] Checking service liveness & readiness...")
    live_resp = client.get("/health/liveness")
    ready_resp = client.get("/health/readiness")
    assert live_resp.status_code == 200, f"Liveness check failed: {live_resp.text}"
    assert ready_resp.status_code == 200, f"Readiness check failed: {ready_resp.text}"
    print("      Liveness: OK, Readiness: OK")

    # 2. Test payload and signature
    print("[2/5] Preparing representative GitHub workflow_run failure payload...")
    payload_dict = {
        "action": "completed",
        "workflow_run": {
            "id": 88442211,
            "name": "CI / Unit Tests",
            "head_branch": "fix/payment-gateway",
            "head_sha": "f3e2d1c0b9a876543210fedcba9876543210abcd",
            "status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.com/crlabs-ai/akesis/actions/runs/88442211",
            "event": "push",
        },
        "repository": {"name": "akesis", "owner": {"login": "crlabs-ai"}},
        "sender": {"login": "octocat"},
    }
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    secret = settings.github_webhook_secret
    signature = f"sha256={hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()}"
    print(f"      Calculated HMAC-SHA256 signature: {signature[:18]}...")

    # 3. Mock GitHub API logs endpoint
    print("[3/5] Mocking GitHub API log retrieval boundary...")
    raw_ci_log = """
[CI Step] Running test suite via pytest
============================= test session starts ==============================
collecting ... collected 8 items

tests/test_gateway.py ..F.....                                           [100%]

=================================== FAILURES ===================================
___________________________ test_stripe_charge_fail ___________________________

    def test_stripe_charge_fail():
>       raise ConnectionRefusedError("Unable to reach payment provider")
E       ConnectionRefusedError: Unable to reach payment provider

tests/test_gateway.py:42: ConnectionRefusedError
=========================== short test summary info ============================
FAILED tests/test_gateway.py::test_stripe_charge_fail - ConnectionRefusedError: Auth timeout
========================= 1 failed, 7 passed in 0.28s ==========================
##[error]Process completed with exit code 1.
"""

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/88442211/logs").respond(
            status_code=200,
            text=raw_ci_log,
        )

        # 4. Dispatch webhook request
        print("[4/5] Sending webhook request to POST /v1/webhooks/github...")
        response = client.post(
            "/v1/webhooks/github",
            content=payload_bytes,
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": signature,
                "Content-Type": "application/json",
            },
        )

        # 5. Verify response and extracted signal
        print(f"[5/5] Response Status: {response.status_code}")
        assert response.status_code == 200, f"Webhook failed: {response.text}"
        data = response.json()
        print("      Response Body:")
        print(f"      - Status: {data.get('status')}")
        print(f"      - Incident ID: {data.get('incident_id')}")
        print(f"      - Classified Category: {data.get('category')}")
        print(f"      - Message: {data.get('message')}")

        assert data["status"] == "accepted"
        assert data["category"] == "test"
        assert "ConnectionRefusedError" in data["message"]
        assert "tests/test_gateway.py" in data["message"]

    print("\nSUCCESS: All smoke test assertions passed cleanly!")


if __name__ == "__main__":
    run_smoke_test()
