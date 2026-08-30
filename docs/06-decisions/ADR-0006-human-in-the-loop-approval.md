# ADR-0006: Human-in-the-Loop Approval Gate & Authoritative Decision Persistence

## Status
Accepted

## Date
2026-08-30

## Context
In Akesis Phase 5, the Docker Sandbox Validation Engine verified generated patches in isolated containers. Before pull requests can be opened on GitHub repositories (Phase 7), Akesis requires an explicit human review gate to satisfy the foundational product commitment: *no unattended or autonomous PR generation occurs without developer authorization*.

Key architectural constraints:
1. **Deterministic Eligibility**: Only validated, high-confidence, passing fixes may reach Slack.
2. **Authoritative Source of Truth**: The application database is the definitive source of truth; Slack UI state is purely downstream and presentation-only.
3. **Idempotency & Concurrency**: Slack retries and multiple button clicks must be safely handled without race conditions or contradictory state transitions.
4. **Security**: Incoming Slack interaction webhooks must be verified using HMAC-SHA256 signatures with replay protection. Rejection notes and user comments are treated as untrusted passive data.

## Decision
1. **Approval Domain & State Machine**: We define `ApprovalStatus` (`pending`, `approved`, `rejected`, `expired`, `cancelled`) in `src/packages/shared/models.py`. Legal transitions are strictly enforced from `pending` to terminal states; re-transitioning between terminal states is rejected with conflict errors.
2. **Slack Block Kit & Client**: We implement `SlackClientProtocol` and `SlackClient` in `src/packages/sdk/slack_client.py` for dispatching structured approval cards and updating message cards via `response_url`.
3. **Approval Orchestrator**: We implement `ApprovalService` in `src/packages/shared/approval_service.py` to evaluate deterministic eligibility, generate approval records with configurable TTL expiration, and execute atomic state transitions.
4. **Interaction Route**: We expose `POST /v1/slack/interactions` in `src/apps/api/routes.py` with HMAC-SHA256 signature verification and 5-minute replay prevention.

## Consequences
### Positive
* Human oversight guaranteed: Zero code is delivered to GitHub before explicit developer approval.
* Idempotent & Resilient: Double-clicks and Slack retries are handled safely with atomic transitions.
* Secure: Tamper-proof payload verification prevents forged callbacks.

### Negative / Limitations
* In V1, approval records operate in-memory with session persistence; full multi-node database synchronization is integrated as part of multi-instance scaling.
