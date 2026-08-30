# User Flows & Lifecycle: Akesis

This document maps the end-to-end operational lifecycle of an Akesis remediation incident.

---

## 1. Remediation Lifecycle Flow

```
[1. Event Ingestion]
        ↓
[2. Context Collection]  ──(Extract Log Signal, Traceback, Target File)
        ↓
[3. Diagnosis]           ──(Categorize: LINT | DEPENDENCY | FLAKY)
        ↓
[4. Remediation]         ──(Synthesize Minimal Unified Diff)
        ↓
[5. Validation]          ──(Docker Sandbox: Compile & Test)
        ↓
[6. Human Decision]      ──(Open Pull Request / Developer Review)
        ↓
[7. Resolution]          ──(Merge PR / Quarantine Test)
```

---

## 2. Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    actor Dev as Developer
    participant GH as GitHub Actions
    participant API as Akesis API Gateway
    participant Agent as Diagnostic Agent
    participant Box as Docker Sandbox
    participant Delivery as PR Delivery Service

    Dev->>GH: git push (Feature Branch)
    GH->>GH: Execute CI Pipeline (Fails)
    GH->>API: Webhook: workflow_run (Failed)
    API->>API: Verify HMAC Signature & Parse Event
    API->>GH: Fetch Job Logs & Commit Diff
    API->>Agent: Run Diagnosis & Patch Synthesis
    Agent->>Agent: Extract Traceback & Generate Minimal Patch
    Agent->>Box: Spin Up Docker Sandbox
    Box->>Box: Apply Patch & Execute Verification Command
    alt Sandbox Validation Succeeds (Exit 0)
        Box-->>Agent: Validation PASSED
        Agent->>Delivery: Format Pull Request Payload
        Delivery->>GH: Open Pull Request with Trace Proof
        GH-->>Dev: Notification: Akesis opened PR
        Dev->>GH: Review Diff & Click "Merge"
    else Sandbox Validation Fails
        Box-->>Agent: Validation FAILED (Non-zero exit)
        Agent->>Agent: Log Failure Telemetry & Abort PR
    end
```
