# Akesis REST API Reference

```text
Document ID: API-001
Status: Authoritative V1 API Specification
Base Path: /
Protocol: HTTP/1.1 over TLS 1.3
Specification Style: RESTful / JSON
```

---

## 1. Overview & Authentication Mechanisms

The Akesis Ingestion Gateway exposes public and webhook-authenticated endpoints. All requests receive a unique correlation identifier returned via the `X-Request-ID` response header.

### Authentication Standards
1. **GitHub Webhooks:** Authenticated using the `X-Hub-Signature-256` header containing an HMAC-SHA256 signature calculated over the raw request payload using `GITHUB_WEBHOOK_SECRET`.
2. **Slack Interactive Callbacks:** Authenticated using the `X-Slack-Signature` header calculated using `SLACK_SIGNING_SECRET` and paired with `X-Slack-Request-Timestamp` to prevent replay attacks (maximum 300-second window).
3. **Health Endpoints:** Unauthenticated public probes for load balancer and orchestrator health checks.

---

## 2. API Endpoints Catalog

| Method | Path | Authentication | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/health/liveness` | None | Liveness probe indicating service process is running |
| `GET` | `/health/readiness` | None | Readiness probe confirming configuration and environment readiness |
| `POST` | `/v1/webhooks/github` | HMAC-SHA256 (`X-Hub-Signature-256`) | Ingests GitHub Actions `workflow_run` failure events |
| `POST` | `/v1/slack/interactions` | HMAC-SHA256 (`X-Slack-Signature`) | Handles interactive button callbacks from Slack approval cards |

---

## 3. Endpoint Specifications

### 3.1 Health Liveness Probe

Returns HTTP 200 indicating the Python application process is healthy and serving traffic.

* **Method:** `GET`
* **Path:** `/health/liveness`
* **Authentication:** None

#### Response (200 OK)
```json
{
  "status": "ok"
}
```

---

### 3.2 Health Readiness Probe

Returns HTTP 200 indicating the application environment is configured.

* **Method:** `GET`
* **Path:** `/health/readiness`
* **Authentication:** None

#### Response (200 OK)
```json
{
  "status": "ready",
  "environment": "development"
}
```

---

### 3.3 Ingest GitHub Webhook Event

Receives, verifies, and processes GitHub `workflow_run` events. Non-failure runs and non-workflow_run events are acknowledged safely with `status: "ignored"`.

* **Method:** `POST`
* **Path:** `/v1/webhooks/github`
* **Headers:**
  * `Content-Type: application/json`
  * `X-GitHub-Event: workflow_run` (Required)
  * `X-Hub-Signature-256: sha256=...` (Required HMAC-SHA256 signature)
  * `X-Request-ID: req_...` (Optional incoming trace ID)

#### Request Body (Truncated Example)
```json
{
  "action": "completed",
  "workflow_run": {
    "id": 123456789,
    "name": "Continuous Integration",
    "head_branch": "feature/login-fix",
    "head_sha": "a1b2c3d4e5f678901234567890abcdef12345678",
    "conclusion": "failure",
    "html_url": "https://github.com/crlabs-ai/akesis/actions/runs/123456789"
  },
  "repository": {
    "name": "akesis",
    "owner": {
      "login": "crlabs-ai"
    }
  },
  "sender": {
    "login": "octocat"
  }
}
```

#### Response (200 OK — Actionable Failure Ingested)
```json
{
  "status": "accepted",
  "incident_id": "inc_9f8e7d6c5b4a",
  "category": "lint",
  "message": "Failure successfully diagnosed: LintViolation(E501) in src/utils.py"
}
```

#### Response (200 OK — Ignored Event or Success Conclusion)
```json
{
  "status": "ignored",
  "incident_id": null,
  "category": null,
  "message": "Workflow run conclusion 'success' is not a failure"
}
```

#### Error Responses
* **`401 Unauthorized`**: Missing or invalid HMAC signature in `X-Hub-Signature-256`.
  ```json
  {
    "detail": "Invalid or missing webhook signature (X-Hub-Signature-256)"
  }
  ```
* **`400 Bad Request`**: Malformed JSON payload.
  ```json
  {
    "detail": "Malformed JSON payload"
  }
  ```
* **`422 Unprocessable Content`**: Missing required `workflow_run` or `repository` objects in payload.
  ```json
  {
    "detail": "Payload missing required 'workflow_run' or 'repository' objects"
  }
  ```

---

### 3.4 Handle Slack Interactive Interactions

Receives user button clicks (`Approve & Open PR` or `Reject Fix`) from Slack Block Kit cards.

* **Method:** `POST`
* **Path:** `/v1/slack/interactions`
* **Headers:**
  * `Content-Type: application/x-www-form-urlencoded`
  * `X-Slack-Signature: v0=...` (Required HMAC signature)
  * `X-Slack-Request-Timestamp: 1725000000` (Required epoch timestamp)

#### Request Body
Standard Slack form-encoded body containing a URL-encoded `payload` JSON parameter:
```
payload=%7B%22actions%22%3A%5B%7B%22action_id%22%3A%22approve_fix%22%2C%22value%22%3A%22appr_inc_123_a1b2c3d4%22%7D%5D%2C%22user%22%3A%7B%22username%22%3A%22lead_engineer%22%2C%22id%22%3A%22U123456%22%7D%2C%22response_url%22%3A%22https%3A%2F%2Fhooks.slack.com%2Factions%2FT00%2FB00%2FXXX%22%7D
```

Decoded `payload` JSON object:
```json
{
  "actions": [
    {
      "action_id": "approve_fix",
      "value": "appr_inc_123_a1b2c3d4"
    }
  ],
  "user": {
    "id": "U123456",
    "username": "lead_engineer"
  },
  "response_url": "https://hooks.slack.com/actions/T00/B00/XXX"
}
```

#### Response (200 OK)
```json
{
  "status": "ok",
  "approval_id": "appr_inc_123_a1b2c3d4",
  "decision": "approved",
  "is_duplicate": false
}
```

#### Error Responses
* **`401 Unauthorized`**: Invalid Slack signature or timestamp older than 300 seconds.
  ```json
  {
    "detail": "Invalid or expired Slack signature"
  }
  ```
* **`400 Bad Request`**: Missing `payload` parameter, missing actions array, or missing approval ID.
  ```json
  {
    "detail": "Missing approval ID in action value"
  }
  ```
* **`404 Not Found`**: Approval ID not found in database.
  ```json
  {
    "detail": "Approval record 'appr_inc_123_a1b2c3d4' not found."
  }
  ```
* **`409 Conflict`**: Illegal state transition (e.g., approval record already in terminal state `rejected` or `expired`).
  ```json
  {
    "detail": "Cannot transition 'rejected' to 'approved'"
  }
  ```
