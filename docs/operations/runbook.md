# Akesis Operational Runbook & Troubleshooting Guide

```text
Document ID: OPS-001
Status: Authoritative V1 Operations Runbook
Audience: Site Reliability Engineers, Platform Engineers & Maintainers
```

---

## 1. Observability, Logging & Correlation Tracking

Akesis uses **`structlog`** to emit structured JSON logs to standard output. Every HTTP request and pipeline step includes correlation identifiers to trace incidents across systems.

### Key Correlation Identifiers

| Identifier | Format | Context / Origin | Description |
| :--- | :--- | :--- | :--- |
| `request_id` | `req_<hex>` | `X-Request-ID` header | Tracks individual HTTP requests through API gateway |
| `incident_id` | `inc_<hex>` | Webhook ingestion | Uniquely identifies a CI/CD failure event |
| `proposal_id` | `fix_<inc>_<sha>` | Fix synthesis | Identifies the generated patch and unified diff |
| `validation_id` | `val_<proposal_id>` | Docker sandbox | Identifies a sandbox verification execution job |
| `approval_id` | `appr_<inc>_<sha>` | Approval gate | Identifies a human review request in database and Slack |
| `mutation_id` | `mut_<hex>` | Git mutation | Identifies a branch mutation and PR creation lifecycle |

### Sample Structured Log Entry
```json
{
  "timestamp": "2026-09-01T12:00:05.123456Z",
  "level": "info",
  "event": "diagnostic_completed",
  "logger": "akesis.diagnostic_service",
  "incident_id": "inc_9f8e7d6c5b4a",
  "category": "lint",
  "confidence": 0.95,
  "evidence_sufficiency": "sufficient",
  "code_snippets_count": 1,
  "duration_ms": 1420.5
}
```

---

## 2. Incident Inspection & Database Tracking

Inspect incident lifecycle state directly in PostgreSQL:

### Check Approval Record Status
```sql
SELECT
    approval_id, incident_id, proposal_id, commit_sha,
    status, reviewer, reason, requested_at, decided_at, expires_at
FROM approvals
WHERE incident_id = 'inc_9f8e7d6c5b4a';
```

### Check Git Mutation & PR Status
```sql
SELECT
    mutation_id, proposal_id, approval_id, branch_name,
    status, pr_number, pr_url, failure_reason, updated_at
FROM mutations
WHERE incident_id = 'inc_9f8e7d6c5b4a';
```

---

## 3. Operational Troubleshooting Runbooks

### Runbook 1: Webhook 401 Unauthorized Errors

* **Symptom:** GitHub webhooks fail with HTTP 401 and response `{"detail": "Invalid or missing webhook signature (X-Hub-Signature-256)"}`.
* **Root Causes:**
  1. Secret mismatch between GitHub Webhook settings and `GITHUB_WEBHOOK_SECRET` in `.env`.
  2. Reverse proxy stripped or altered the `X-Hub-Signature-256` header.
  3. Payload body was modified during proxy forwarding.
* **Resolution Steps:**
  1. Inspect incoming logs for `"Rejected webhook due to invalid HMAC signature"`.
  2. Verify that `GITHUB_WEBHOOK_SECRET` in `.env` matches the secret configured in GitHub repository webhook settings.
  3. Ensure reverse proxy passes raw request headers and body verbatim without parsing or buffering mutations.

---

### Runbook 2: Gemini LLM Rate Limits (429) or Timeouts

* **Symptom:** Logs show `LLMRateLimitError` or `LLMTimeoutError`; `DiagnosticResult` falls back to `confidence_score = 0.0`.
* **Root Causes:**
  1. Google Generative AI API quota exceeded.
  2. Network latency or DNS resolution timeout connecting to `generativelanguage.googleapis.com`.
* **Resolution Steps:**
  1. Check quota and billing status in Google AI Studio / Google Cloud Console.
  2. Check `GEMINI_TIMEOUT_SECONDS` in `.env` (default is 30.0s; increase to 45.0s if experiencing high latency).
  3. Verify host DNS resolution: `curl -I https://generativelanguage.googleapis.com/v1beta/models`.

---

### Runbook 3: Docker Sandbox Execution Failures

* **Symptom:** Sandbox validation returns `status: "infrastructure_error"` or `DockerUnavailableError`.
* **Root Causes:**
  1. Docker daemon is stopped, crashed, or unreachable at `/var/run/docker.sock`.
  2. The `akesis` application user lacks permissions to communicate with the Docker socket (`dial unix /var/run/docker.sock: permission denied`).
  3. Validator image `akesis-validator:v1` has not been built on the host.
* **Resolution Steps:**
  1. Check Docker daemon status: `sudo systemctl status docker`.
  2. Check socket permissions: `sudo usermod -aG docker akesis`.
  3. Verify validator image exists: `docker images | grep akesis-validator`.
  4. If missing, rebuild validator image:
     ```bash
     docker build -f docker/Dockerfile.validator -t akesis-validator:v1 .
     ```
  5. Test container execution manually:
     ```bash
     docker run --rm --network none akesis-validator:v1 pytest --version
     ```

---

### Runbook 4: Slack Interaction Signature or Replay Failures

* **Symptom:** Reviewer clicks "Approve & Open PR" in Slack, but card displays an error or endpoint returns HTTP 401.
* **Root Causes:**
  1. `SLACK_SIGNING_SECRET` does not match the Slack App credentials.
  2. Host system clock is drifting by more than 300 seconds (5 minutes) relative to Slack's servers.
* **Resolution Steps:**
  1. Verify host clock synchronization using NTP: `timedatectl status` or `ntpstat`.
  2. Verify `SLACK_SIGNING_SECRET` in `.env` matches the App Signing Secret under Slack App Basic Information.

---

### Runbook 5: Stale Commit SHA Mutation Aborts

* **Symptom:** Mutation service fails with `StaleCommitError: Stale commit detected: Checked out HEAD does not match approved proposal commit`.
* **Root Causes:**
  1. A developer pushed new commits to the target feature branch between the time the CI failure occurred and the human review approval.
* **Operational Handling:**
  1. This is a **safety invariant by design**. Akesis will NOT push a fix generated for an older commit on top of a branch that has moved forward.
  2. The new push on GitHub will trigger a new CI workflow run. If it fails, Akesis will ingest the new commit and generate a fresh diagnosis and proposal.

---

### Runbook 6: Webhook Verification Retries, Holds and Exhaustion

**Symptoms:** GitHub deliveries answer `503` with a `Retry-After` header, or `422` with the detail `Delivery verification retries exhausted`. The retry worker reports `not ready` in its health probe.

**Processes:** the API serves webhooks and the operator endpoint. The retry worker is a separate process (`python -m src.apps.worker`) that executes scheduled verification attempts. Both share PostgreSQL. Either can restart without losing state.

**Check worker readiness:**

```bash
/opt/akesis/.venv/bin/python -m src.apps.worker.healthcheck
```

Exit code 0 means the heartbeat is fresh and the worker is `ok`. Exit code 1 prints the reason: `heartbeat stale`, `not ready: database_unavailable`, `not ready: github_app_not_configured`, or `no readable heartbeat`.

**List deliveries that need attention (authenticated, read-only):**

```bash
curl -s -H "X-Akesis-Read-Token: $READ_API_TOKEN" \
  "http://127.0.0.1:8000/v1/webhook-deliveries?status=exhausted&status=retryable_error&limit=50"
```

Each item shows `delivery_id`, `status`, `failure_code`, `last_error_code`, `attempt_count`, `max_attempts`, `next_retry_at`, `received_at`, `updated_at` and `event_type`. It never shows payloads, signatures, headers, installation identifiers or credentials. Page with the returned `next_cursor`.

**What each state means:**

| Status | Meaning | Action |
| :--- | :--- | :--- |
| `retryable_error`, `next_retry_at` in the future | A transient failure is scheduled. Attempts remain. | No action. The worker runs it when due. |
| `retryable_error`, `next_retry_at` in the past | Due. The worker claims it on its next pass (about 15 seconds). | If it stays here, check worker readiness. |
| `provider_hold` | GitHub asked for a delay longer than the 24-hour sanity ceiling. The delay is honoured in full. | No action needed. The worker releases it at the scheduled time. Confirm the delay is expected. |
| `exhausted` (`failure_code = verification_retries_exhausted`) | All permitted attempts were used. Terminal. | Diagnose using `last_error_code` (see below). Do not reopen the row. |
| `rejected` | A non-retryable verification failure. | Diagnose `failure_code`. This status is never retried. |

**Diagnosing exhaustion using `last_error_code`:**

- `github_rate_limited` or `github_api_error`: rate limits or an upstream outage. Wait, then re-run the workflow.
- `github_app_http_error`, `installation_suspended`, `invalid_installation_token` or `github_network_error`: check the GitHub App ID, private key, installation status and GitHub reachability.
- `app_not_configured`: `GITHUB_APP_ID` or `GITHUB_APP_PRIVATE_KEY` is missing.
- `identity_incomplete`: the stored identity for the delivery is incomplete. Re-run the workflow.
- `verification_deadline_exceeded`: one verification exceeded `WEBHOOK_VERIFICATION_DEADLINE_SECONDS` (default 240). Usually GitHub is slow or rate-limiting. The delivery is retried on its own schedule.

**Recovery:**

1. Fix the root cause.
2. Re-run the workflow run in GitHub. A re-run creates a new delivery with a new delivery ID, so the terminal row is never reopened.

**Do not** update `webhook_events` rows by hand. Manual edits bypass the atomic transitions and leave no audit trail. Authenticated operator requeue is deliberately not implemented; see ADR-0010.

**Shutdown behaviour:** a clean stop finishes only the delivery in flight and releases any claimed delivery it did not start. Released deliveries show `retryable_error` with `next_retry_at` at the time of the stop, and their attempt count is unchanged. If a stop coincides with a database outage, claims may remain `verifying` until the stale window (10 minutes) recovers them.

**Stuck-worker procedure:** if the health probe reports `heartbeat stale`, the worker has stopped making progress. systemd restarts a worker whose watchdog expires (`WatchdogSec=600s`), so a frozen worker is normally restarted on its own. If the probe stays stale, check the journal, then restart with `sudo systemctl restart akesis-worker`. A worker that is alive but waiting out a database outage keeps pinging and is not restarted; fix the database instead. A clean stop removes the heartbeat directory, so the probe reports `no readable heartbeat` after a stop, which is expected. Restarting is safe: a claim in flight becomes reclaimable after 10 minutes, and that reclaim counts as an attempt.

**Logs:** the worker logs structured events `webhook_worker_database_unavailable`, `webhook_worker_verification_interrupted`, `webhook_worker_tick_failed` and `webhook_worker_started`. Logs record exception types, never messages, so connection strings and tokens are not written.

## 4. Routine Maintenance Procedures

### 4.1 Ephemeral Workspace Cleanup
Akesis cleans up temporary directories in `finally:` blocks. If the host process is killed forcefully (`SIGKILL`), orphan temporary directories in `/tmp` may remain:

```bash
# Clean up orphaned sandbox workspaces older than 24 hours
find /tmp -maxdepth 1 -type d -name "akesis_val_*" -mtime +1 -exec rm -rf {} +
find /tmp -maxdepth 1 -type d -name "akesis_mut_*" -mtime +1 -exec rm -rf {} +
```

### 4.2 Pruning Docker Build Cache & Dangling Containers
```bash
# Clean stopped containers and dangling images
docker container prune -f --filter "until=24h"
docker image prune -f
```

### 4.3 Database Backup & Restore
```bash
# Backup PostgreSQL database
pg_dump -U postgres -d akesis -F c -f /var/backups/akesis_$(date +%Y%m%d_%H%M%S).dump

# Restore PostgreSQL database
pg_restore -U postgres -d akesis /var/backups/akesis_backup_file.dump
```

### 4.4 Rotating Credentials
1. **Rotate GitHub Webhook Secret:** Update GitHub Webhook settings $\rightarrow$ Update `GITHUB_WEBHOOK_SECRET` in `.env` $\rightarrow$ Reload systemd service (`sudo systemctl restart akesis`).
2. **Rotate Gemini API Key:** Create new key in Google AI Studio $\rightarrow$ Update `GEMINI_API_KEY` in `.env` $\rightarrow$ Reload service.
3. **Rotate Slack Signing Secret:** Regenerate in Slack App settings $\rightarrow$ Update `SLACK_SIGNING_SECRET` in `.env` $\rightarrow$ Reload service.
