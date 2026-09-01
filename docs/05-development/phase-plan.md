# Engineering Phase Plan: Akesis V1 Authoritative Roadmap

```text
Status: Active Authoritative Roadmap
Revision: 2026-09-01 (Reconciled with Phases 0–7 Implementation)
Source of Truth: ADR-0001 through ADR-0007, AGENTS.md
```

---

## Historical Note & Superseded Initial Plan

> [!NOTE]
> The initial pre-implementation draft outlined an early 5-phase structure that combined diagnosis, context retrieval, patch synthesis, and PR delivery. During implementation, Akesis adopted a rigorous, safety-first 9-phase sequence that decoupled AI diagnosis, exact commit context retrieval, deterministic patch validation, Docker sandbox execution, human approval persistence, and controlled Git mutation. The original 5-phase draft is preserved in project archives for historical intent and is superseded by the authoritative 9-phase roadmap below.

---

## Authoritative 9-Phase Engineering Sequence

### Phase 0: Project Foundation & Architecture
* [x] **Status:** `COMPLETE` (ADR-0001)
* Established authoritative documentation hierarchy (`docs/00` through `docs/06`).
* Standardized exclusively on Python 3.12+ and `uv`.
* Defined `AGENTS.md` operational guidelines, zero-scope-drift boundaries, and strict stopping conditions.

### Phase 1: GitHub Failure Ingestion & Signal Parsing
* [x] **Status:** `COMPLETE`
* FastAPI Webhook Gateway (`/v1/webhooks/github`) with HMAC-SHA256 constant-time verification.
* CI log streaming, ANSI stripping, and deterministic regex/AST signal extraction (`FailureSignal`, `FailureContext`).
* Unit and integration test suite with 100% verified HMAC security.

### Phase 2: Gemini Structured Diagnostic Baseline
* [x] **Status:** `COMPLETE` (ADR-0002)
* Provider-agnostic LLM client (`GeminiClient`) supporting structured generation.
* Pydantic diagnostic schemas (`DiagnosisProposal`, `DiagnosticResult`, `RemediationDirection`).
* Bounded confidence scoring and deterministic evidence item extraction.

### Phase 3: Codebase Context Retrieval & Exact Commit Alignment
* [x] **Status:** `COMPLETE` (ADR-0003)
* `GitRepositoryCheckoutManager` checking out source code at the exact failing commit SHA.
* Deterministic `CodebaseContextResolver` extracting bounded, line-numbered `CodeEvidence`.
* Token budgeting and evidence packaging (`EvidencePackage`).

### Phase 4: Deterministic Fix Proposal Engine & Patch Validator
* [x] **Status:** `COMPLETE` (ADR-0004)
* `FixService` prompting Gemini for structured fix proposals (`RawFixProposal`).
* `PatchValidator` enforcing unified diff syntax, max 2 files / 120 lines / 8KB limits.
* Target-file grounding against codebase evidence and rejection of path traversals / protected workflow files.

### Phase 5: Isolated Docker Sandbox Validation Engine
* [x] **Status:** `COMPLETE` (ADR-0005)
* Dedicated validator image (`docker/Dockerfile.validator` → `akesis-validator:v1`).
* Isolated execution (`--network none`, `--cap-drop ALL`, non-root, read-only mounts).
* Verbatim patch application (`git apply --check` $
ightarrow$ `git apply` without whitespace fixing).
* Deterministic command selection matrix (`pytest`, `ruff`, `mypy`, `python -m compileall`).

### Phase 6: Human-in-the-Loop Approval Gate & PostgreSQL Persistence
* [x] **Status:** `COMPLETE` (ADR-0006)
* Durable PostgreSQL persistence (`approvals` table, `ApprovalModel`, `ApprovalRepository`).
* Alembic async migration setup (`0001_create_approvals.py`).
* Slack interactive Block Kit card dispatch and HMAC-SHA256 signature verification.
* Atomic conditional state transitions (`pending` $
ightarrow$ `approved`, `rejected`, `expired`, `cancelled`).

### Phase 7: Controlled Git Mutation & GitHub PR Creation
* [x] **Status:** `COMPLETE` (ADR-0007)
* Strict pre-flight checks: proposal validity, sandbox validation pass (exit 0), and approved human status.
* Stale commit SHA abort mechanism protecting against out-of-sync mutation.
* Deterministic fix branch naming (`akesis/fix/<incident-id>/<proposal-id>`) and base branch protection.
* Pre-push Docker validation verifying patched working tree before remote push.
* GitHub Pull Request creation via extended `GitHubClientProtocol` with structured audit trail.
* Durable persistence in PostgreSQL table `mutations` (`0002_create_mutations.py`).

---

## Upcoming Phases

### Phase 8: End-to-End Orchestrator Pipeline
* [x] **Status:** `COMPLETE` (ADR-0008)
* **RemediationOrchestrator:** Coordinate the end-to-end pipeline across all Phase 1–7 components.
* **Non-Blocking Ingestion:** Webhook gateway immediately returns `202 Accepted` and delegates pipeline execution to an asynchronous background task.
* **Approval Pause & Resume:** Pipeline dispatches Slack interactive cards and pauses; human decision at `/v1/slack/interactions` resumes `GitMutationService` to create the PR.
* **Idempotency & Deduplication:** Guard against duplicate webhook deliveries and concurrent approval callbacks using database constraints.
* **Safety Invariant:** Human approval remains mandatory; zero autonomous mutation or auto-merging.

### Phase 9: Vertical Slice Validation, Benchmark Hardening & Release Sign-Off
* [x] **Status:** `COMPLETE` (ADR-0009)
* **Benchmark Evaluation Suite:** 10–12 realistic CI failure scenarios covering Lint (Ruff), Dependency (missing imports/lockfiles), and Test (pytest assertion) errors.
* **Resilience & Latency Testing:** Verify timeout handling, sandbox resource limits, and error diagnostics across all pipeline stages.
* **Audit Trail Verification:** Ensure complete traceability from failing GitHub Actions run $
ightarrow$ Slack card $
ightarrow$ PostgreSQL state $
ightarrow$ Pull Request.
* **V1 Release Sign-Off:** Final quality gate execution and production runbook validation.
