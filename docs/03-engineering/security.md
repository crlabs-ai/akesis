# Security Policy & Architecture Standards: Akesis V1

---

## 1. Secrets Management & Production Validation
* **Zero Hardcoding:** No API tokens, webhook secrets, or credentials may be committed.
* **Static Scanning:** CI runs `gitleaks` on all pull requests.
* **Runtime Config:** Load credentials from environment variables (`.env` in local dev).
* **Fail-Fast Production Validation:** In `production` environment, application startup enforces non-empty, non-placeholder secrets (`DATABASE_URL`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GEMINI_API_KEY`, `SLACK_WEBHOOK_URL`, `SLACK_SIGNING_SECRET`).
* **Secret Isolation:** The sign-in secrets (`GITHUB_APP_CLIENT_SECRET`, `AUTH_TOKEN_ENCRYPTION_KEY`) must be distinct from all GitHub, Gemini and Slack credentials, and the worker and executor refuse to start with them set.
* **GitHub App authentication:** Sign short-lived RS256 App JWTs with `PyJWT[crypto]`; use the App ID or client ID as issuer. Verify installation ownership with the App JWT before issuing an installation token. Keep the private key and installation tokens out of logs, Git configuration, and validation workspaces. Installation tokens are short-lived and must be refreshed before expiry.
  The App authentication adapter is not yet wired into webhook processing or repository operations; its presence does not establish repository provenance or make the remediation path production-ready.
* **Webhook verification retries:** Network errors, HTTP 429, HTTP 5xx, and HTTP 403 responses indicating a rate limit are retryable. Identity mismatches, inaccessible installations and suspended installations are rejected. Each delivery has at most `webhook_retry_max_attempts` (default 5) verification attempts, counting the initial attempt. Provider-directed delays from `Retry-After` or `x-ratelimit-reset` are honoured in full and never shortened by the local cap. Non-finite values are invalid and fall back to computed backoff. A provider delay above `webhook_retry_provider_delay_ceiling_seconds` (default 24 hours) becomes an operator-visible `provider_hold`, released when due, and never silently becomes an earlier retry. Computed backoff is exponential from 60 seconds with jitter, capped at 15 minutes. Early redeliveries are refused without consuming an attempt or calling GitHub. A delivery whose attempts are spent becomes terminal `exhausted`, is refused with HTTP 422 on every later redelivery, and never calls GitHub again. A separate retry worker (`python -m src.apps.worker`) executes due retries. It claims deliveries with `FOR UPDATE SKIP LOCKED` and the same atomic transitions, and it rebuilds verification input only from identity fields persisted after the original HMAC verification. It never stores or reads raw payloads, signatures, installation tokens or private keys. A claimed attempt that crashes is reclaimed after ten minutes and counts as an attempt, because verification may already have started. A single verification has a total deadline (`webhook_verification_deadline_seconds`, default 240 seconds, validated below the ten-minute window). A verification that exceeds it is recorded as a retryable `verification_deadline_exceeded` failure, so a live verification is never reclaimed while it is still running. Downstream admission is shared with the webhook route and never runs remediation. The unconditional remediation hold ([ADR-0010](../06-decisions/ADR-0010-temporary-remediation-hold.md)) remains in effect.
* **Operator visibility of deliveries:** `GET /v1/workspaces/{workspace_id}/deliveries` lists `retryable_error`, `provider_hold` and `exhausted` deliveries of the workspace's linked installations. It is read-only and requires a signed-in member (401 without a session, 404 for non-members). Its response model is an explicit allow-list of ten fields. It never exposes payloads, payload digests, signatures, request headers, installation identifiers, source SHAs, tokens or private keys. Pagination is keyset on `(updated_at, delivery_id)`, and cursors are opaque and validated. Error responses never echo the submitted session.

---

## 2. API Security & Access Control
* **GitHub sign-in and sessions ([ADR-0012](../06-decisions/ADR-0012-identity-and-workspaces.md)):**
  * Sign-in is the Akesis GitHub App's user authorization: authorization code with PKCE (S256) via Authlib. `state` and the PKCE verifier are server-side, hashed, single use, expire in ten minutes and are bound to the starting browser by an HttpOnly cookie. Users are keyed on the numeric GitHub user ID.
  * Sessions are opaque random tokens in the `akesis_session` cookie (HttpOnly, `SameSite=Lax`, `Secure` in production). PostgreSQL stores only their SHA-256; expiry and revocation are checked on every request. Logout and "sign out everywhere" revoke immediately.
  * GitHub user tokens are Fernet-encrypted at rest, used only server-side, and deleted on revocation. No GitHub credential, App key or access token reaches browser code; browser storage holds only the selected workspace ID.
  * State-changing routes require `X-Akesis-CSRF: 1` and reject a foreign `Origin`.
  * The global read token (`X-Akesis-Read-Token`) is removed.
* **Tenant isolation:**
  * Every tenant read goes through a `WorkspaceScope`, issued only by `tenancy.authorize()` after a fresh membership check, and filtered in SQL by the workspace's linked installations. Non-members receive 404, members without the role 403.
  * Nobody changes their own role; roles are granted only below the actor's own; the last owner cannot be removed. Revoked members lose access on their next request.
  * Invitations are bound to the invitee's immutable numeric GitHub ID (resolved at creation); usernames are display-only, so renames cannot transfer an invitation.
  * Installation linking: a user-account installation only by that user; an organization installation only into a team workspace and only by a verified, active organization owner; then ownership is verified with an App-issued installation token. One installation, one workspace.
  * Slack approvals additionally require the Slack user to be mapped (by an owner or admin) to an APPROVER-or-higher member of the incident's workspace, checked when recorded and again when the job runs. The job refuses a pipeline whose installation differs from its verified provenance.
* **Webhook Authentication:**
  * GitHub Ingestion: Authenticated via SHA-256 HMAC signature (`X-Hub-Signature-256`) against raw payload bytes.
  * Slack Interactions: Authenticated via SHA-256 HMAC signature (`X-Slack-Signature`) with strict 5-minute timestamp replay window verification (`X-Slack-Request-Timestamp`).
* **Public Health Probes:**
  * `/health/liveness` and `/health/readiness` remain publicly accessible for container orchestrator health checks.
* **Perimeter & Deployment Requirements:**
  * Serve the console and the API from the same site (a reverse proxy mapping `/v1` to the API), so the `SameSite=Lax` session cookie is first-party. An identity-aware proxy remains optional defence in depth.

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
   * There is no network-enabled path: every validation container runs with `--network none`, and dependencies come only from the pre-built image.
5. **Read-only root filesystem (Phase 1.13):** containers run with `--read-only`. The only writable paths are the per-run workspace copy bind-mounted at `/workspace` and a `/tmp` tmpfs (`rw,exec,nosuid,nodev,size=64m,mode=1777`). The tmpfs is memory-backed, counts against `--memory`, and is private to each container. Without it, Python's `tempfile` silently falls back to the working directory, so pytest's `tmp_path` would land in the workspace on host disk. `$HOME`, `/usr`, `/etc` and `/var/tmp` are not writable. Bytecode writing is disabled in the image (`PYTHONDONTWRITEBYTECODE=1`); the pytest, mypy and ruff caches go to the workspace. `/tmp` allows exec on purpose: the workspace is already writable and executable and pytest runs repository code, so `noexec` would add no barrier and would fail legitimate tests that run scripts they wrote.
6. **Other enforced container flags:** `--network none`, `--cap-drop ALL`, `--security-opt no-new-privileges`, `--user` set to the numeric UID:GID that owns the private (0700) workspace, never root, `--pids-limit 256`, `--memory` and `--cpus` from settings, and `--rm`. A timeout, a cancellation or any other error force-removes the container by name (`docker rm -f`).
7. **Validation executor (Phase 1.14a):** with `SANDBOX_EXECUTOR_URL` set, validation runs only in the separate executor process, reached over a Unix socket (0660, with a SO_PEERCRED UID allowlist). Jobs are signed with HMAC-SHA256 under a request key, and results under a separate response key that binds each response to its request. The executor checks the hold, replay, expiry, schema, every object ID, the tree, the digest, the paths and the patch before running the unchanged container flags, and it refuses to start with `.env` or application credentials present. Failures fail closed, with no fallback to local Docker. See `docs/02-architecture/validation-executor.md`.

---

## 5. Prompt Injection Defense & Data Sanitization
* Every log-derived field (error type, message, file, frames, excerpt, job, step) is placed inside one `<untrusted_ci_log id="...">` block whose id is random per prompt, so log text cannot close it. Code fences in log text are neutralized. Verified metadata (repository, commit, run) is a separate section taken from provenance, never from the log.
* System instructions state that the block is evidence only and cannot change instructions, grant permissions, request secrets, approve or merge changes, or skip validation. Gemini receives no tools: it returns schema-constrained JSON, which still passes the patch validator, Docker validation and human approval.
* **Workflow log retrieval (Phase 1.10):** the worker fetches logs only after re-verifying the persisted provenance in PostgreSQL; a rejected check makes no network request. The installation token is limited to the event repository with `actions:read` and `metadata:read`. The run attempt must match the verified run, attempt, SHA and repositories. The signed download URL must be HTTPS on port 443 without credentials, on an allowed host (`LOG_DOWNLOAD_HOSTS`), resolving only to public addresses; it is fetched without an Authorization header, proxies or `.netrc`, follows no further redirect, is size-capped while streaming, and is never logged. Archives are read in memory under entry, size and compression-ratio limits; links, devices, encrypted entries and unsafe paths are rejected. Recognised secret formats (a fixed list of token formats, private keys and secret-named key/value assignments, not universal secret detection) are redacted across the whole log file before any excerpt is cut, and again at prompt time.
* Output is constrained to strict Pydantic JSON schemas.
* Diagnostic and error logging paths sanitize all secret keys, tokens, and database passwords.
* **Worker process and heartbeat:** the worker runs as its own process with no inbound listener. It needs the GitHub App private key and a database connection. Its heartbeat holds only a state, a timestamp and a PID. It is written to a random temporary file created in a service-owned runtime directory (`0750`) and renamed into place. The file is `0644` so the readiness probe can read it. The heartbeat has never been written through a predictable name.
* **Operator API risks (known, not mitigated in code):** one shared read token guards every read endpoint, so it grants all read access and identifies no operator. Reads are not audited per caller. The application applies no rate limiting, so rate limiting belongs at the reverse proxy. The worker and the API use one database role, so the worker is not limited to the tables it needs. Both processes hold the GitHub App key.
* **Remediation entry points (Phase 15):** the hold is the first statement of each public entry point that could mutate a repository: `process_failure`, `resume_approval` and `create_pull_request`. No database read or preflight happens before it. An invalid provenance record therefore fails closed as `UnverifiedProvenanceError`. The provenance verifier's specific rejection codes are asserted directly. The webhook route schedules no remediation. A census test enforces the call sites.
* **Known blockers before the hold is lifted:** the API and worker share one database role, one read token guards all reads, and the API and worker share one database role. Docker daemon access was removed from the API in Phase 1.14b, and approval execution moved to the remediation worker in Phase 1.14c. The API now only records decisions and holds no executor keys, socket access or Docker access. Only the executor user is in the `docker` group, and only the worker reaches the executor. Fork-sourced mutation and the PR base were resolved in Phase 1.7, and the writable sandbox root in Phase 1.13. See ADR-0010, "Additional removal blockers".
