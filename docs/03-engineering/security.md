# Security Policy & Architecture Standards: Akesis V1

---

## 1. Secrets Management & Production Validation
* **Zero Hardcoding:** No API tokens, webhook secrets, or credentials may be committed.
* **Static Scanning:** CI runs `gitleaks` on all pull requests.
* **Runtime Config:** Load credentials from environment variables (`.env` in local dev).
* **Fail-Fast Production Validation:** In `production` environment, application startup enforces non-empty, non-placeholder secrets (`DATABASE_URL`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_TOKEN`, `GEMINI_API_KEY`, `SLACK_WEBHOOK_URL`, `SLACK_SIGNING_SECRET`).
* **Secret Isolation:** `READ_API_TOKEN` must be distinct from all GitHub, Gemini, and Slack credentials.

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
