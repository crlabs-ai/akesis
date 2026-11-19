# Security Policy & Architecture Standards: Akesis V1

---

## 1. Secrets Management & Production Validation
* **Zero Hardcoding:** No API tokens, webhook secrets, or credentials may be committed.
* **Static Scanning:** CI runs `gitleaks` on all pull requests.
* **Runtime Config:** Load credentials from environment variables (`.env` in local dev).
* **Fail-Fast Production Validation:** In `production` environment, application startup enforces non-empty, non-placeholder secrets (`DATABASE_URL`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GEMINI_API_KEY`, `SLACK_WEBHOOK_URL`, `SLACK_SIGNING_SECRET`).
* **Secret Isolation:** `READ_API_TOKEN` must be distinct from all GitHub, Gemini, and Slack credentials.
* **GitHub App authentication:** Sign short-lived RS256 App JWTs with `PyJWT[crypto]`; use the App ID or client ID as issuer. Verify installation ownership with the App JWT before issuing an installation token. Keep the private key and installation tokens out of logs, Git configuration, and validation workspaces. Installation tokens are short-lived and must be refreshed before expiry.
  The App authentication adapter is not yet wired into webhook processing or repository operations; its presence does not establish repository provenance or make the remediation path production-ready.
* **Webhook verification retries:** Network errors, HTTP 429, HTTP 5xx, and HTTP 403 responses indicating a rate limit are retryable. Identity mismatches, inaccessible installations and suspended installations are rejected. Each delivery has at most `webhook_retry_max_attempts` (default 5) verification attempts, counting the initial attempt. Provider-directed delays from `Retry-After` or `x-ratelimit-reset` are honoured in full and never shortened by the local cap. Non-finite values are invalid and fall back to computed backoff. A provider delay above `webhook_retry_provider_delay_ceiling_seconds` (default 24 hours) becomes an operator-visible `provider_hold`, released when due, and never silently becomes an earlier retry. Computed backoff is exponential from 60 seconds with jitter, capped at 15 minutes. Early redeliveries are refused without consuming an attempt or calling GitHub. A delivery whose attempts are spent becomes terminal `exhausted`, is refused with HTTP 422 on every later redelivery, and never calls GitHub again. A separate retry worker (`python -m src.apps.worker`) executes due retries. It claims deliveries with `FOR UPDATE SKIP LOCKED` and the same atomic transitions, and it rebuilds verification input only from identity fields persisted after the original HMAC verification. It never stores or reads raw payloads, signatures, installation tokens or private keys. A claimed attempt that crashes is reclaimed after ten minutes and counts as an attempt, because verification may already have started. A single verification has a total deadline (`webhook_verification_deadline_seconds`, default 240 seconds, validated below the ten-minute window). A verification that exceeds it is recorded as a retryable `verification_deadline_exceeded` failure, so a live verification is never reclaimed while it is still running. Downstream admission is shared with the webhook route and never runs remediation. The unconditional remediation hold ([ADR-0010](../06-decisions/ADR-0010-temporary-remediation-hold.md)) remains in effect.
* **Operator visibility of deliveries:** `GET /v1/webhook-deliveries` lists `retryable_error`, `provider_hold` and `exhausted` deliveries. It is read-only and requires the read API token; it returns 401 without a valid token, and fails closed in production when no token is configured. Its response model is an explicit allow-list of ten fields. It never exposes payloads, payload digests, signatures, request headers, installation identifiers, source SHAs, tokens or private keys. Pagination is keyset on `(updated_at, delivery_id)`, and cursors are opaque and validated. Error responses never echo the submitted token.

---

## 2. API Security & Access Control
* **Read API Token Protection (`X-Akesis-Read-Token`):**
  * Read endpoints (`GET /v1/incidents`, `GET /v1/incidents/{incident_id}`, `GET /v1/pipelines`) are guarded by `verify_read_token`.
  * Development mode (token empty): permits local access without credentials.
  * Configured mode (token set): requires constant-time HMAC header verification (`hmac.compare_digest`), rejecting unauthorized requests with 401 Unauthorized.
  * Submitted tokens are never logged or echoed in responses.
* **Webhook Authentication:**
  * GitHub Ingestion: Authenticated via SHA-256 HMAC signature (`X-Hub-Signature-256`) against raw payload bytes.
  * Slack Interactions: Authenticated via SHA-256 HMAC signature (`X-Slack-Signature`) with strict 5-minute timestamp replay window verification (`X-Slack-Request-Timestamp`).
* **Public Health Probes:**
  * `/health/liveness` and `/health/readiness` remain publicly accessible for container orchestrator health checks.
* **Perimeter & Deployment Requirements:**
  * In production, the API must sit behind an authenticated reverse proxy (e.g., Cloudflare Access, AWS ALB with OIDC/Cognito, or Nginx). The `read_api_token` must be supplied server-side and never exposed in browser client bundles or public repositories.

---

## 3. Network & Host Protection
* **Trusted Host Enforcement:** `TrustedHostMiddleware` validates HTTP `Host` headers against explicit allowed hosts (`TRUSTED_HOSTS`). Wildcard `*` is strictly disallowed in production.
* **CORS Policy:** `CORSMiddleware` restricts cross-origin access to explicit allowlisted origins (`CORS_ALLOWED_ORIGINS`). `allow_credentials` defaults to `False`. Wildcard `*` origins are rejected in production.

---

## 4. Docker Sandbox Security Policy
The sandbox executes untrusted AI-generated code under strict isolation:
1. **Non-Root Execution:** Runs as unprivileged user (`uid 1000`).
2. **Resource Limits:** Capped at 512MB RAM and 1.0 CPU core with a 30-second timeout.
3. **Temporary Workspace:** Code executed in ephemeral temporary directories.
4. **Network Policy:**
   * **Default:** Network access is **disabled** (`--network none`).
   * **Controlled Exception:** Dependency installation workflows may explicitly enable network access strictly for downloading packages. Once installation completes, validation commands execute with network disabled.

---

## 5. Prompt Injection Defense & Data Sanitization
* Untrusted CI logs are enclosed within explicit XML boundary tags in prompt templates.
* Prompts instruct the model to treat log contents strictly as raw data.
* Output is constrained to strict Pydantic JSON schemas.
* Diagnostic and error logging paths sanitize all secret keys, tokens, and database passwords.
* **Worker process and heartbeat:** the worker runs as its own process with no inbound listener. It needs the GitHub App private key and a database connection. Its heartbeat holds only a state, a timestamp and a PID. It is written to a random temporary file created in a service-owned runtime directory (`0750`) and renamed into place. The file is `0644` so the readiness probe can read it. The heartbeat has never been written through a predictable name.
* **Operator API risks (known, not mitigated in code):** one shared read token guards every read endpoint, so it grants all read access and identifies no operator. Reads are not audited per caller. The application applies no rate limiting, so rate limiting belongs at the reverse proxy. The worker and the API use one database role, so the worker is not limited to the tables it needs. Both processes hold the GitHub App key.
* **Remediation entry points (Phase 15):** the hold is the first statement of each public entry point that could mutate a repository: `process_failure`, `resume_approval` and `create_pull_request`. No database read or preflight happens before it. An invalid provenance record therefore fails closed as `UnverifiedProvenanceError`. The provenance verifier's specific rejection codes are asserted directly. The webhook route schedules no remediation. A census test enforces the call sites.
* **Known blockers before the hold is lifted:** fork-sourced mutations target the source repository, the pull request base is the failing run's branch, the sandbox root filesystem is writable, the API and worker share one database role, and one read token guards all reads. See ADR-0010, "Additional removal blockers".
