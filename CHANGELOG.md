# Changelog

All notable changes to the **Akesis** platform are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] - 2026-09-01

### Added
* **Phase 0 (Project Foundation):** Established specification-driven documentation baseline, Python 3.12+ runtime, and `uv` package manager (ADR-0001).
* **Phase 1 (Webhook Ingestion & Parsing):** Implemented FastAPI Webhook Gateway (`/v1/webhooks/github`) with HMAC-SHA256 signature verification and regex-based ANSI stripping/log signal extraction (`FailureSignal`, `FailureContext`).
* **Phase 2 (Gemini Structured Diagnosis):** Implemented provider-agnostic `LLMClientProtocol` and `GeminiClient` with schema-enforced Pydantic generation (`DiagnosisProposal`, `DiagnosticResult`) and 0.0 confidence fallback (ADR-0002).
* **Phase 3 (Codebase Context Retrieval):** Implemented `GitRepositoryCheckoutManager` for exact commit SHA checkout and `CodebaseContextResolver` with bounded 40-line windowing and path traversal security (ADR-0003).
* **Phase 4 (Fix Proposal Engine):** Implemented `FixProposalService` and `PatchValidator` with unified diff parsing (`FilePatch`, `PatchHunk`), strict budget caps (max 2 files / 100 lines / 4KB), protected file shielding, and evidence grounding (ADR-0004).
* **Phase 5 (Isolated Docker Sandbox Validation):** Implemented `DockerSandboxRunner` and `ValidationService` executing ephemeral container verifications with `--network none`, `--cap-drop ALL`, non-root user `akesis` (`uid 1000`), and verbatim `git apply` (ADR-0005).
* **Phase 6 (Human Approval Gate & Persistence):** Implemented `ApprovalService`, `SlackClient` Block Kit card dispatch, `/v1/slack/interactions` callback endpoint, and async PostgreSQL persistence for table `approvals` with atomic state transitions (ADR-0006).
* **Phase 7 (Controlled Git Mutation & PR Delivery):** Implemented `GitMutationService` and `GitHubClient` with preflight invariant verification, stale commit SHA aborts, deterministic branch naming (`akesis/fix/...`), pre-push Docker validation, GitHub PR opening, and PostgreSQL table `mutations` (ADR-0007).
* **Modernized Documentation Suite:** Added comprehensive High-Level Design (`docs/architecture/HLD.md`), Low-Level Design (`docs/architecture/LLD.md`), REST API Reference (`docs/api/README.md`), Deployment Guide (`docs/deployment/README.md`), Operations Runbook (`docs/operations/runbook.md`), and updated root `README.md`.
