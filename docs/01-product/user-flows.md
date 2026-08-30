# User Flows & Lifecycle: Akesis

This document maps the end-to-end operational lifecycle of an Akesis remediation incident.

---

## 1. End-to-End Remediation Lifecycle Diagram

```mermaid
sequenceDiagram
    autonumber
    actor Dev as Developer
    participant GH as GitHub Actions
    participant Ingress as Akesis Ingress API
    participant Orch as Orchestration Engine
    participant Agent as Diagnostic Agent
    participant Box as Docker Sandbox
    participant Delivery as PR Delivery Service

    Dev->>GH: git push (Feature Branch)
    GH->>GH: Execute CI Pipeline (Fails)
    GH->>Ingress: Webhook: workflow_run (Failed)
    Ingress->>Ingress: Verify HMAC Signature & Parse Event
    Ingress->>Orch: Dispatch Incident Event
    Orch->>GH: Fetch Raw Job Logs & Commit Diff
    Orch->>Agent: Execute Diagnostic & Patch Loop
    Agent->>Agent: Extract Root Cause & Synthesize Unified Diff
    Agent->>Box: Spin Up Ephemeral Container
    Box->>Box: Apply Patch & Execute Verification Command
    alt Sandbox Validation Succeeds (Exit 0)
        Box-->>Orch: Validation PASSED
        Orch->>Delivery: Construct Pull Request Payload
        Delivery->>GH: Open Pull Request with Trace Proof
        GH-->>Dev: Notification: Akesis opened PR
        Dev->>GH: Review Diff & Click "Merge"
        GH->>GH: Re-run CI (Green)
    else Sandbox Validation Fails
        Box-->>Orch: Validation FAILED (Non-zero exit)
        Orch->>Orch: Log Failure Telemetry & Quarantine Incident
        Orch->>GH: Post Diagnostic Failure Comment (Optional)
    end
```

---

## 2. Step-by-Step Lifecycle Specification

### Step 1: Detection & Ingestion
*   **Input:** GitHub Webhook (`workflow_run.completed` with `conclusion: "failure"`).
*   **Security:** Verify `X-Hub-Signature-256` using repository webhook secret.
*   **Action:** Extract Repository ID, Branch, Commit SHA, Workflow Run ID, and Job IDs. Create `Incident` record with state `INGESTED`.

### Step 2: Context Collection
*   **Action:** Fetch raw job logs via GitHub API. Download commit patch diff between base branch and feature branch.
*   **Sanitization:** Strip ANSI terminal color codes, mask known secret patterns (`ghp_*`, `AKIA*`, Bearer tokens).

### Step 3: Diagnosis & Patch Generation
*   **Action:** Pass sanitized log tail (last 500 lines) + relevant file AST context to the Diagnostic Agent.
*   **Output:** Structured JSON schema containing:
    *   `category`: `LINT` | `DEPENDENCY` | `FLAKY_TEST`
    *   `root_cause`: Concise explanation of why the build failed.
    *   `patch`: Unified git diff.
    *   `confidence`: Float between 0.0 and 1.0.

### Step 4: Sandbox Validation
*   **Action:** Provision ephemeral Docker container matching repository runtime (e.g., Python 3.12, Node 20).
*   **Execution:** Mount workspace as copy-on-write $ightarrow$ apply git patch $ightarrow$ execute exact validation command (e.g., `npm run lint`, `pytest tests/test_parser.py`).
*   **Criteria:** Exit code must be 0. Stdout/stderr must contain 0 errors.

### Step 5: Human Delivery
*   **Action:** Delivery service creates a branch `akesis/fix-<incident-id>` and opens a PR against the user's feature branch.
*   **PR Content:** Includes Root Cause summary, Exact command used for validation, and before/after log snippet.
