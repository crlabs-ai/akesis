# Integration Architecture: Akesis

This document specifies external third-party integrations, communication protocols, authentication, and failure behaviors.

---

## 1. GitHub Integration (GitHub App)
*   **Protocol:** HTTPS / REST API v3 & Webhooks.
*   **Authentication:** 
    *   Inbound: Webhook secret verification using HMAC-SHA256 (`X-Hub-Signature-256`).
    *   Outbound: Ephemeral JSON Web Token (JWT) exchanged for short-lived GitHub App Installation Access Tokens (valid for 60 minutes).
*   **Required Permissions:**
    *   `checks:read` (Read CI check runs)
    *   `contents:write` (Create fix branches and commit diffs)
    *   `pull_requests:write` (Open and comment on PRs)
    *   `actions:read` (Download workflow execution logs)
*   **Failure & Retry:** Ingress implements exponential backoff with jitter on GitHub API rate limits ($429 / 403$).

---

## 2. LLM Provider Integration (Frontier Models)
*   **Protocol:** HTTPS / TLS 1.3 REST API.
*   **Authentication:** Secret bearer tokens stored in cloud secret managers.
*   **Model Strategy:** Primary: Claude 3.5 Sonnet / GPT-4o; Fallback: Gemini 1.5 Pro.
*   **Failure & Fallback:** If primary provider returns 5xx or exceeds 15-second latency timeout, circuit breaker switches to secondary provider.

---

## 3. Container Runtime Integration (Docker Engine)
*   **Protocol:** Unix Socket (`/var/run/docker.sock`) or remote Docker TLS daemon.
*   **Sandboxing:** Ephemeral container creation with `--network none` (post-dependency fetch), `--cap-drop ALL`, `--read-only`, and resource memory caps (`512MB`).
