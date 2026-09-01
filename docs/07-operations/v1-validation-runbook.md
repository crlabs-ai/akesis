# Akesis V1 Validation & Operations Runbook

```text
Document: V1 Operational Runbook & Verification Manual
Version: 1.0.0-rc1
Author: CRLabs Engineering
Source of Truth: ADR-0001 through ADR-0009, AGENTS.md
```

---

## 1. Purpose
This runbook provides the authoritative, step-by-step procedures for deploying, configuring, testing, and operating Akesis V1 in local and staging environments. It explicitly separates **Automated Regression Testing** (deterministic mocks) from **Live Integration Smoke Testing** (real GitHub App, Slack workspace, and Gemini API credentials).

---

## 2. V1 Scope
Akesis V1 provides human-supervised, self-healing remediation for Python CI/CD failures:
* **Supported Failure Types:** Pytest test failures, Ruff linting/formatting errors, and Python missing dependency / `pyproject.toml` misconfigurations.
* **Bounded Modifiability:** Maximum 2 target files, 100 unified diff lines, 4,000 patch characters.
* **Target Boundaries:** Python codebases with GitHub Actions CI workflows. Protected CI files (`.github/workflows/*`) and path traversals are strictly rejected.
* **Zero Autonomous Mutations:** Zero code pushes or pull requests without explicit human approval in PostgreSQL. Zero autonomous auto-merges.

---

## 3. Architecture Flow

```text
GitHub Actions CI Failure
          │
          ▼
1. Webhook Gateway (/v1/webhooks/github)
   ├── Constant-time HMAC-SHA256 signature verification
   ├── Failure payload extraction (repository, run_id, commit_sha)
   └── Returns HTTP 202 Accepted (asynchronous processing)
          │
          ▼
2. RemediationOrchestrator (Background Task)
   ├── Check / Create PipelineRecord in PostgreSQL (`status: received`)
   ├── Extract raw CI log and parse failure signal (Ruff / Pytest / Dependency)
   │
   ├── Stage: Diagnosis (`status: diagnosing`)
   │   └── Gemini structured root-cause diagnosis (is_fixable, confidence score)
   │
   ├── Stage: Codebase Context Retrieval
   │   └── Clone/checkout exact commit SHA & extract bounded context lines
   │
   ├── Stage: Fix Proposal Generation (`status: proposing`)
   │   ├── Gemini structured unified diff synthesis
   │   └── Strict patch validation (syntax, line/file budgets, path safety)
   │
   ├── Stage: Sandbox Validation (`status: validating`)
   │   ├── Materialize ephemeral sandbox workspace
   │   ├── Apply patch verbatim (`git apply --check` -> `git apply`)
   │   └── Execute validator container (`akesis-validator:v1`, `--network none`, `--cap-drop ALL`)
   │
   └── Stage: Human Authorization Gate (`status: awaiting_approval`)
       ├── Persist FailureContext, FixProposal, and ValidationResult in PostgreSQL
       └── Post interactive Block Kit approval card to Slack channel
          │
          ▼
3. Human Decision (Slack Interaction Callback /v1/slack/interactions)
   ├── Verify Slack HMAC-SHA256 signature & timestamp (<5 min)
   ├── Atomic conditional state update in PostgreSQL (`pending` -> `approved`/`rejected`)
   └── Dispatch resume_approval background task
          │
          ▼
4. Controlled Git Mutation & PR Delivery (`status: mutating` -> `completed`)
   ├── Reload authoritative FailureContext, FixProposal, ValidationResult from PostgreSQL
   ├── Verify exact proposal_id, commit_sha, valid status, and passed validation
   ├── Check checked-out repository HEAD against base_commit_sha (prevent stale drift)
   ├── Create deterministic branch (`akesis/fix/<incident-id>/<proposal-id>`)
   ├── Apply validated diff and commit authoritatively
   ├── Push branch to remote GitHub repository
   └── Open GitHub Pull Request with complete diagnostic audit trail
```

---

## 4. Prerequisites
* **Operating System:** Linux (Ubuntu 22.04+ recommended) or macOS.
* **Python Runtime:** Python 3.12+ (managed via `uv`).
* **Container Runtime:** Docker Engine 24+ with non-root user execution permissions.
* **Database:** PostgreSQL 16+ (Docker container or native service).
* **Git:** Git 2.40+ installed locally.

---

## 5. Python & `uv` Setup
```bash
# Clone the repository
git clone https://github.com/crlabs-ai/akesis.git
cd akesis

# Install uv package manager (if missing)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync dependencies and create virtual environment
uv sync
```

---

## 6. PostgreSQL Setup
Akesis uses PostgreSQL 16 for durable state persistence (`approvals`, `mutations`, `pipelines`).

### Local Docker Container (Port 5436)
To prevent host port conflicts with existing native PostgreSQL instances on port 5432, Akesis maps port `5436` on the host to port `5432` in the container:

```bash
# Start Akesis PostgreSQL container
docker run -d \
  --name akesis-postgres \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgrespassword \
  -e POSTGRES_DB=akesis \
  -p 5436:5432 \
  postgres:16-alpine

# Verify container is healthy
docker ps --filter "name=akesis-postgres"
```

---

## 7. Docker Requirements & Sandbox Validator Image
The deterministic sandbox validation engine requires the `akesis-validator:v1` Docker image:

```bash
# Build the dedicated validator image
docker build -t akesis-validator:v1 -f docker/Dockerfile.validator .

# Verify image availability
docker images akesis-validator:v1
```

---

## 8. Environment Variables
Copy `.env.example` to `.env` and configure appropriate values:

```bash
cp .env.example .env
```

### Key Reference
| Variable | Description | Example / Default |
| :--- | :--- | :--- |
| `ENVIRONMENT` | Runtime mode (`development`, `test`, `production`) | `development` |
| `LOG_LEVEL` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |
| `DATABASE_URL` | Async PostgreSQL connection string | `postgresql+asyncpg://postgres:postgrespassword@localhost:5436/akesis` |
| `GITHUB_WEBHOOK_SECRET` | Secret to sign/verify GitHub webhooks (HMAC-SHA256) | `your_github_webhook_secret` |
| `GITHUB_TOKEN` | GitHub Personal Access Token or App Token for API access | `ghp_xxxxxxxxxxxx` |
| `GITHUB_API_URL` | Base GitHub API REST endpoint | `https://api.github.com` |
| `GEMINI_API_KEY` | Google Gemini API key | `AIzaxxxxxxxxxxxx` |
| `GEMINI_MODEL` | Gemini model name | `gemini-1.5-flash` |
| `SANDBOX_IMAGE` | Docker validator image tag | `akesis-validator:v1` |
| `SANDBOX_BASE_DIR` | Host directory for ephemeral sandboxes | `/tmp/akesis/sandbox` |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL | `https://hooks.slack.com/services/...` |
| `SLACK_SIGNING_SECRET` | Secret to verify Slack interaction payloads | `your_slack_signing_secret` |
| `SLACK_CHANNEL_ID` | Slack channel ID for approval notifications | `C0123456789` |
| `APPROVAL_TTL_HOURS` | Expiration window for human approvals (hours) | `24` |

---

## 9. Alembic Migration Procedure
Run migrations to create and update `approvals`, `mutations`, and `pipelines` tables:

```bash
# Apply migrations to head
uv run alembic upgrade head

# Check current revision status
uv run alembic current
```

---

## 10. Local API Startup
```bash
# Start FastAPI application server with Uvicorn
uv run uvicorn src.apps.api.main:app --host 0.0.0.0 --port 8000 --reload
```
Check health endpoint:
```bash
curl http://localhost:8000/health
# Response: {"status":"healthy","environment":"development"}
```

---

## 11. GitHub App & Webhook Setup
1. In your GitHub repository or organization, go to **Settings > Webhooks > Add webhook**.
2. **Payload URL:** `https://<your-public-url>/v1/webhooks/github` (use `ngrok` or Cloudflare Tunnel for local testing).
3. **Content type:** `application/json`.
4. **Secret:** Set the same value as `GITHUB_WEBHOOK_SECRET`.
5. **Events:** Select **Workflow runs**.

---

## 12. Slack App Setup
1. In the Slack API portal, create a new Slack App in your workspace.
2. Enable **Incoming Webhooks** and add a webhook to your target alerts channel.
3. Under **Interactivity & Shortcuts**, enable Interactivity and set the **Request URL** to:
   `https://<your-public-url>/v1/slack/interactions`
4. Copy the **Signing Secret** to `SLACK_SIGNING_SECRET` in `.env`.

---

## 13. Required Secrets & Configuration Checklist
* [ ] `DATABASE_URL` configured and reachable on port 5436.
* [ ] `GITHUB_WEBHOOK_SECRET` shared between GitHub and Akesis `.env`.
* [ ] `GITHUB_TOKEN` with `repo` permissions to clone, push branches, and create PRs.
* [ ] `GEMINI_API_KEY` active with quota for `gemini-1.5-flash`.
* [ ] `SLACK_WEBHOOK_URL` and `SLACK_SIGNING_SECRET` configured.
* [ ] `akesis-validator:v1` Docker image built locally.

---

## 14. Automated Benchmark Execution
The automated benchmark suite runs completely isolated from external networks and requires zero external credentials:

```bash
# Run all 12 vertical-slice benchmark scenarios
uv run pytest -v tests/benchmark/

# Run complete test suite with coverage
uv run pytest -v --cov=src --cov-report=term-missing tests/
```

---

## 15. Live Smoke-Test Preparation
> [!IMPORTANT]
> Live integration testing involves real network calls, GitHub commits/branches, Slack notifications, and Gemini API token consumption. Never run live smoke tests against critical production repositories or branches.

---

## 16. Controlled Test Repository & Branch Requirements
1. Create a dedicated sandbox repository, e.g., `crlabs-ai/akesis-smoke-test`.
2. Ensure default branch is `main`.
3. Configure a GitHub Actions workflow `.github/workflows/ci.yml` that runs tests:
```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install pytest ruff
      - run: pytest
```

---

## 17. How to Trigger a Deliberately Safe CI Failure
Push a deliberate, benign test failure to a feature branch (e.g., `test/smoke-failure`):

```python
# test_app.py
def test_addition():
    assert 1 + 1 == 3  # Deliberate failure
```

Push to GitHub:
```bash
git checkout -b test/smoke-failure
git add test_app.py
git commit -m "test: introduce deliberate smoke failure"
git push origin test/smoke-failure
```

---

## 18. Expected Akesis Pipeline Behavior
1. GitHub Actions workflow runs and fails.
2. Webhook payload is received by `/v1/webhooks/github`.
3. Webhook returns HTTP `202 Accepted` within 50ms.
4. Akesis retrieves CI logs, identifies `AssertionError: assert 1 + 1 == 3`.
5. Akesis clones repository at exact failing commit SHA.
6. Gemini synthesizes a patch:
```diff
--- a/test_app.py
+++ b/test_app.py
@@ -1,2 +1,2 @@
 def test_addition():
-    assert 1 + 1 == 3
+    assert 1 + 1 == 2
```
7. Sandbox runner applies patch and runs `pytest` in `akesis-validator:v1`.
8. Validation succeeds with exit code 0.
9. Pipeline state is updated in PostgreSQL to `awaiting_approval`.

---

## 19. Expected Slack Approval Behavior
1. An interactive Block Kit message appears in the configured Slack channel.
2. Message contains:
   * Repository, branch, and failing commit SHA.
   * Root cause diagnosis and confidence score.
   * Formatted unified diff.
   * Sandbox validation execution logs (`pytest: passed`).
   * Interactive buttons: `[Approve & Open PR]` and `[Reject]`.

---

## 20. Expected Git Mutation Behavior
1. Operator clicks `[Approve & Open PR]`.
2. Slack dispatches payload to `/v1/slack/interactions`.
3. Akesis validates Slack signature, updates approval in PostgreSQL to `approved`.
4. Orchestrator resumes pipeline, reloads exact persisted proposal and validation.
5. Verifies target repository HEAD has not drifted.
6. Creates local branch `akesis/fix/<incident-id>/<proposal-id>`.
7. Applies patch verbatim and commits with structured message.
8. Pushes branch to origin.

---

## 21. Expected Pull Request Result
A new Pull Request appears on GitHub with:
* Title: `[Akesis] Fix for CI failure in run #<run_id>`
* Target branch: `test/smoke-failure` (or origin branch).
* Description containing diagnosis summary, sandbox command output, human approver tag, and audit trail.
* Clean diff repairing `test_app.py`.

---

## 22. Rejection Path
If the operator clicks `[Reject]` in Slack:
1. Approval record is updated to `rejected` in PostgreSQL.
2. Pipeline transitions to `rejected`.
3. Orchestrator halts closed. Zero branches created, zero commits pushed, zero PRs opened.

---

## 23. Validation Failure Path
If Gemini generates a patch that fails `pytest` in the sandbox:
1. Validator container exits with non-zero code.
2. Pipeline transitions to `failed`.
3. No Slack approval request is posted.
4. Orchestrator logs failure reason and halts.

---

## 24. Stale-Commit Safety Path
If the target branch receives new commits while approval is pending:
1. Operator approves in Slack.
2. Mutation pre-flight checks detect remote HEAD does not match `proposal.commit_sha`.
3. `StaleCommitError` is raised.
4. Mutation halts immediately before creating a branch or pushing commits.
5. Pipeline transitions to `failed` with explicit stale-commit error message.

---

## 25. Duplicate Webhook & Idempotency Behavior
* Duplicate GitHub webhook deliveries for the same `incident_id` are detected via PostgreSQL unique constraints on `incident_id` and return the existing pipeline record without duplicating work.
* Duplicate Slack button clicks return `is_duplicate=True` and log an idempotent duplicate warning without opening multiple PRs.

---

## 26. Logs & Observability
Akesis uses structured JSON logging (`structlog`). Important log keys:
* `pipeline_started`: Indicates new incident ingestion.
* `pipeline_awaiting_approval`: Proposal validated; awaiting Slack decision.
* `approval_decision_committed`: Human choice recorded in DB.
* `pipeline_completed_successfully`: PR opened on GitHub.
* `pipeline_halted_*`: Pipeline halted due to safety or validation constraint.

---

## 27. Cleanup Procedure
```bash
# Remove ephemeral checkouts and sandboxes
rm -rf /tmp/akesis/repos/*
rm -rf /tmp/akesis/sandbox/*

# Stop PostgreSQL container (if using Docker)
docker stop akesis-postgres && docker rm akesis-postgres
```

---

## 28. Troubleshooting Guide
| Symptom | Probable Cause | Action |
| :--- | :--- | :--- |
| `HTTP 401 Unauthorized` on webhook | Secret mismatch | Verify `GITHUB_WEBHOOK_SECRET` matches GitHub webhook settings. |
| `HTTP 403 Forbidden` on Slack | Timestamp / Secret mismatch | Verify server clock and `SLACK_SIGNING_SECRET`. |
| `DockerDaemonUnavailable` | Docker not running | Start Docker Engine (`sudo systemctl start docker`). |
| `StaleCommitError` | Target branch updated | Trigger a fresh CI run on latest HEAD. |
| `Database connection error (5436)` | Container stopped | Check `docker ps -a` and restart `akesis-postgres`. |

---

## 29. V1 Acceptance Checklist
- [ ] Automated regression suite passes: `uv run pytest -v tests/`
- [ ] 12 benchmark scenarios pass: `uv run pytest -v tests/benchmark/`
- [ ] PostgreSQL schema is at head: `uv run alembic current`
- [ ] Docker validator image is built: `docker images akesis-validator:v1`
- [ ] Codebase passes static analysis: `ruff`, `mypy`, `diff --check`
- [ ] Live integration prerequisites verified in staging environment.

---

## 30. Known Limitations & Post-V1 Roadmap
* **Single-Repository Scope:** V1 operates within a single GitHub repository context; multi-repo dependency graphs are scheduled for V2.
* **Non-Python Languages:** V1 focuses on Python (`pytest`, `ruff`, `pyproject.toml`); TypeScript/Go/Rust support is planned for V2.
* **Manual Approval Mandatory:** Autonomous auto-merging is deliberately disabled in V1 by design for safety.
