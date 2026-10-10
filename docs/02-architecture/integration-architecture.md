# Integration Architecture: Akesis V1

This document specifies the integration protocols, authentication methods, and payloads for all external systems interfacing with Akesis V1.

---

## 1. GitHub Integration

* **Protocol:** HTTPS / REST API v3 & Webhooks.
* **Authentication:**
  * **Inbound (Webhook):** HMAC-SHA256 signature verification over request body using `GITHUB_WEBHOOK_SECRET` passed in header `X-Hub-Signature-256`.
  * **Outbound (REST API):** GitHub Personal Access Token or GitHub App Installation Token via `Bearer` token in `Authorization` header (`GITHUB_TOKEN`).
* **Endpoints Used:**
  * `GET /repos/{owner}/{repo}/actions/runs/{run_id}/logs` (Fetch raw workflow run console logs)
  * `GET /repos/{owner}/{repo}/commits/{commit_sha}` (Retrieve commit metadata)
  * `GET /repos/{owner}/{repo}/pulls` (Search for existing open PRs)
  * `POST /repos/{owner}/{repo}/pulls` (Create new reviewable Pull Request)
* **Required Token Permissions:** `contents:write`, `pull_requests:write`, `actions:read`.

---

## 2. Google Gemini AI Integration

* **Protocol:** HTTPS / REST API over TLS 1.3.
* **Base URL:** `https://generativelanguage.googleapis.com/v1beta`
* **Authentication:** API Key passed via query parameter (`key={GEMINI_API_KEY}`).
* **Model:** Primary configured model `gemini-1.5-flash` (`settings.gemini_model`).
* **Generation Method:** Structured JSON generation using `responseMimeType: "application/json"` and `responseSchema` derived from Pydantic model schemas (`DiagnosisProposal`, `RawFixProposal`).
* **Error Handling:** Standardized error mapping for HTTP 401/403 (Auth), HTTP 429 (Rate Limit), and connection timeouts.

---

## 3. Slack Workspace Integration

* **Protocol:** HTTPS / REST API & Interactive Block Kit Webhooks.
* **Inbound Authentication:** HMAC-SHA256 signature verification using `SLACK_SIGNING_SECRET` from header `X-Slack-Signature` with 300-second timestamp validation (`X-Slack-Request-Timestamp`).
* **Outbound Dispatch:** Posts rich Block Kit cards containing incident metadata, rationale, unified diff snippet, and "Approve & Open PR" / "Reject Fix" interactive buttons to `SLACK_WEBHOOK_URL`.
* **In-Place Updates:** Updates original message cards via Slack `response_url` upon reviewer decision.

---

## 4. Container Runtime (Docker Engine)

* **Protocol:** Unix Socket (`/var/run/docker.sock`) via asynchronous process execution.
* **Image:** Dedicated validator environment `akesis-validator:v1` built from `docker/Dockerfile.validator` based on `python:3.12-slim`.
* **Containment Flags:**
  * `--network none` (Network access disabled by default)
  * `--cap-drop ALL` (Drop all Linux capabilities)
  * `--security-opt no-new-privileges` (Prevent privilege escalation)
  * `--user 1000:1000` (Run as unprivileged `akesis` user)
  * `--memory 512m` (512 MB memory ceiling)
  * `--cpus 1.0` (1.0 CPU quota)
  * `-v /tmp/akesis/sandbox/...:/workspace:rw` (Ephemeral workspace mount)
