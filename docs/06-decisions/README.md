# Architecture Decision Records (ADR) Index

This directory contains the authoritative, immutable log of all architectural decisions made for Akesis V1.

```text
Status: Approved Authoritative Index
Decision Baseline: ADR-0001 through ADR-0007
Format: Markdown Architectural Decision Records (MADR)
```

---

## 1. ADR Format & Lifecycle

Every Architecture Decision Record follows the MADR format:
* **Status:** `Proposed` | `Accepted` | `Rejected` | `Deprecated` | `Superseded`
* **Structure:** Status $\rightarrow$ Context $\rightarrow$ Decision $\rightarrow$ Consequences (Positive & Limitations).

---

## 2. ADR Master Index

| ADR ID | Title | Status | Date | Scope / Core Decision |
| :--- | :--- | :---: | :---: | :--- |
| **[ADR-0001](ADR-0001-project-foundation.md)** | Specification-Driven, Documentation-First Project Foundation | `Accepted` | 2026-08-30 | Standardize on Python 3.12+, `uv`, `structlog`, and docs under `docs/`. |
| **[ADR-0002](ADR-0002-gemini-diagnostic-baseline.md)** | Gemini AI Diagnostic Baseline & Provider-Agnostic Interface | `Accepted` | 2026-08-30 | Provider-agnostic `LLMClientProtocol` with structured Pydantic schemas. |
| **[ADR-0003](ADR-0003-codebase-context-retrieval.md)** | Deterministic Codebase Context Retrieval & Exact Commit Alignment | `Accepted` | 2026-08-30 | GitPython exact commit checkouts with bounded line windowing. |
| **[ADR-0004](ADR-0004-fix-proposal-engine.md)** | Fix Proposal Engine & Deterministic Patch Validation | `Accepted` | 2026-08-30 | Unified diff representation with strict budget and grounding validation. |
| **[ADR-0005](ADR-0005-docker-sandbox-validation.md)** | Docker Sandbox Validation Engine & Command Isolation | `Accepted` | 2026-08-30 | Isolated `akesis-validator:v1` execution with `--network none` and verbatim `git apply`. |
| **[ADR-0006](ADR-0006-human-in-the-loop-approval.md)** | Human-in-the-Loop Approval Gate & Decision Persistence | `Accepted` | 2026-08-30 | Slack Block Kit approval cards with atomic PostgreSQL state persistence. |
| **[ADR-0007](ADR-0007-github-pr-creation.md)** | Safe GitHub Pull Request Creation & Controlled Git Mutation | `Accepted` | 2026-09-01 | Controlled Git branch mutation, stale SHA verification, and PR creation. |
