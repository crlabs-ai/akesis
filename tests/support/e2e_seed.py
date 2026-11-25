"""Creates representative deliveries through the signed GitHub webhook endpoint.

Only the public ingestion path is used: each delivery is HMAC-signed with the configured secret and
POSTed to /v1/webhooks/github. Nothing is written to the database directly. Installation identifiers
select the GitHub stub behaviour (see tests/support/github_stub.py).

Usage: python -m tests.support.e2e_seed <api_base_url> <secret_file>
"""

import hashlib
import hmac
import json
import sys
import uuid
from pathlib import Path

import httpx

from tests.support.github_stub import RUN_HEAD_SHA, SUCCESS_INSTALLATION

SHORT_RETRY_INSTALLATION = 1002
LONG_RETRY_INSTALLATION = 1003
HOLD_INSTALLATION = 1004
REPO = {"id": 101, "full_name": "base/repo", "name": "repo", "owner": {"login": "base"}}


def _payload(installation: int, run_id: int, sha: str) -> dict[str, object]:
    return {
        "action": "completed",
        "installation": {"id": installation},
        "repository": REPO,
        "workflow_run": {
            "id": run_id,
            "run_attempt": 1,
            "name": "CI",
            "html_url": f"https://github.com/base/repo/actions/runs/{run_id}",
            "head_sha": sha,
            "head_branch": "feature/e2e",
            "head_repository": {"id": 101, "full_name": "base/repo"},
            "conclusion": "failure",
        },
    }


def _post(client: httpx.Client, secret: bytes, payload: dict[str, object]) -> httpx.Response:
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return client.post(
        "/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": signature,
            "X-GitHub-Delivery": f"e2e-{uuid.uuid4()}",
        },
    )


def main() -> None:
    base_url, secret_file = sys.argv[1], Path(sys.argv[2])
    secret = secret_file.read_text(encoding="utf-8").strip().encode()
    run_seed = 9_000_000_000 + (uuid.uuid4().int % 1_000_000_000)
    sha_ok = RUN_HEAD_SHA  # the SHA the stub reports for the authoritative run
    sha_other = "b" * 40
    outcomes: dict[str, list[int]] = {}
    plan = [
        ("verified", SUCCESS_INSTALLATION, 3, sha_ok),
        ("retryable_long", LONG_RETRY_INSTALLATION, 26, sha_other),
        ("hold", HOLD_INSTALLATION, 2, sha_other),
        ("short_retry", SHORT_RETRY_INSTALLATION, 4, sha_other),
    ]
    counter = run_seed
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        for label, installation, count, sha in plan:
            statuses: list[int] = []
            for _ in range(count):
                counter += 1
                statuses.append(
                    _post(client, secret, _payload(installation, counter, sha)).status_code
                )
            outcomes[label] = statuses
    for label, statuses in outcomes.items():
        print(f"{label}: {len(statuses)} deliveries, HTTP {sorted(set(statuses))}")


if __name__ == "__main__":
    main()
