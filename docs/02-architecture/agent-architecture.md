# AI Agent Architecture: Akesis V1

This document defines the runtime agent design, state representations, tool interfaces, and decision boundaries.

---

## 1. Agent Design: Bounded Deterministic State Machine
Akesis V1 rejects unpredictable, open-ended autonomous agent loops. The agent operates as a **finite state machine with bounded transitions**:

```
[Receive Incident Context]
        ↓
[Classify Failure Category] ──(LINT | DEPENDENCY | FLAKY)
        ↓
[Gather Code Context]        ──(Extract Target File & Traceback Lines)
        ↓
[Propose Remediation]       ──(Synthesize Minimal Unified Diff)
        ↓
[Calculate Confidence]      ──(Evaluate Error Signal Clarity)
        ↓
[Execute Sandbox Validation]──(Assert Exit Code 0 in Docker)
        ↓
[Decision Gate]             ──(Pass: Deliver PR / Fail: Log & Abort)
```

---

## 2. Runtime Agent Constraints
* **Structured Outputs Only:** All model outputs must strictly validate against Pydantic schemas.
* **Evidence-Based:** Must ground fixes directly in provided log tracebacks.
* **Minimal Diff Preference:** Must modify only lines required to resolve the failure.
* **Zero Bypass:** Must never bypass sandbox validation or human PR approval.
* **Defer on Uncertainty:** If confidence $< 0.8$ or sandbox validation fails, the agent aborts PR generation and logs diagnostic telemetry.
