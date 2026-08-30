# Integration Architecture: Akesis V1

---

## 1. GitHub Integration (GitHub App)
* **Protocol:** HTTPS / REST API v3 & Webhooks.
* **Authentication:** 
    * Inbound: Webhook secret verification using HMAC-SHA256 (`X-Hub-Signature-256`).
    * Outbound: Short-lived GitHub App Installation Access Tokens.
* **Permissions:** `checks:read`, `contents:write`, `pull_requests:write`, `actions:read`.

---

## 2. LLM Provider Interface
* **Protocol:** HTTPS / TLS 1.3 REST API.
* **Design:** Provider-agnostic client interface calling the primary configured model (e.g., Claude 3.5 Sonnet / GPT-4o).
* **Output:** Strict Pydantic JSON schema validation.

---

## 3. Container Runtime (Docker Engine)
* **Protocol:** Unix Socket (`/var/run/docker.sock`).
* **Isolation:** Non-root execution (`uid 1000`), resource caps (512MB RAM, 1 CPU core), temporary directory mounting.
* **Network Policy:** Disabled by default (`--network none`), enabled explicitly only during dependency installation steps.
