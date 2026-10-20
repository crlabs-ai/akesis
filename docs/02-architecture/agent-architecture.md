# AI Agent Architecture: Akesis V1

This document defines the runtime agent design, state representations, tool interfaces, and decision boundaries.

```text
Status: Approved V1 Authoritative Agent Specification
Agent Model: Bounded Deterministic State Machine
LLM Interface: Provider-Agnostic Google Gemini REST Adapter
Decision Baseline: ADR-0002, ADR-0004, ADR-0006
```

---

## 1. Agent Design: Bounded Deterministic State Machine

Akesis V1 explicitly rejects unpredictable, open-ended autonomous agent loops. The agent operates as a **finite state machine with bounded, verifiable transitions**:

```
[Receive Incident Context]
        ↓
[Classify Failure Category] ──(LINT | DEPENDENCY | TEST | BUILD)
        ↓
[Gather Code Context]        ──(Extract Target File & Bounded Traceback Window)
        ↓
[Diagnose Root Cause]       ──(Structured JSON Schema via Gemini)
        ↓
[Synthesize Fix Proposal]   ──(Minimal Unified Diff with Budget Enforcement)
        ↓
[Execute Sandbox Validation]──(Assert Exit Code 0 in Isolated Docker)
        ↓
[Human Approval Gate]       ──(Dispatch Interactive Card to Slack & Await Authorization)
        ↓
[Controlled Git Mutation]   ──(Pre-Push Check, Branch Commit & PR Delivery)
```

---

## 2. Runtime Agent Constraints & Guardrails

* **Structured Outputs Only:** All model interactions require strict Pydantic JSON schema enforcement (`DiagnosisProposal` and `RawFixProposal`).
* **Evidence Grounding:** Fix proposals are restricted to files present in the failure evidence package or verified to exist in the repository tree.
* **Minimal Diff Principle:** Patches are capped at a maximum of 2 files, 100 diff lines, and 4,000 characters.
* **Protected File Blocking:** Modifications to `.github/workflows/`, `.git/`, `.env*`, and binary files are rejected immediately.
* **Zero Bypass Invariant:** No fix proposal can reach Slack without passing Docker sandbox validation (`exit_code == 0`), and no Pull Request can be opened without explicit human approval.
* **Confidence Threshold:** If diagnostic or fix confidence is below $0.60$ (`settings.min_fix_confidence_threshold`), automated fix generation is withheld.
* **Passive Untrusted Data Boundary:** CI logs and source code comments are treated strictly as passive data to prevent prompt injection attacks.
