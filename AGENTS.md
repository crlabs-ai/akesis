# AGENTS.md — Operational Instructions for AI Agents in Akesis

## 1. Purpose & Authority
This document is the authoritative instruction manual for all AI coding agents (including Antigravity, OpenCode, Codex, and Claude) operating within the `akesis` repository.

Akesis is an engineering-first platform developed by CRLabs AI. Every contribution must meet strict standards of architectural soundness, evidence-based reasoning, and software engineering rigor.

---

## 2. Document Authority Hierarchy
When resolving requirements or architectural decisions, strictly follow this precedence:

1. **LEVEL 1:** Explicit decisions made by the project owner (user prompts / instructions).
2. **LEVEL 2:** Approved product documentation (`docs/00-foundation/`, `docs/01-product/`).
3. **LEVEL 3:** Approved architecture documentation and ADRs (`docs/02-architecture/`, `docs/06-decisions/`).
4. **LEVEL 4:** Approved engineering standards (`docs/03-engineering/`, `docs/04-ai/`).
5. **LEVEL 5:** Existing validated implementation code.
6. **LEVEL 6:** General software engineering best practices.
7. **LEVEL 7:** AI inference.

**CRITICAL RULE:** An AI agent must **NEVER** use inference (Level 7) to override or modify an explicit project decision (Levels 1–4).

---

## 3. Mandatory Workflow for AI Agents

Every agent task must strictly follow this cycle:

```
[1. Read AGENTS.md]
        ↓
[2. Read Relevant Docs] ──(Check docs/00 through docs/06)
        ↓
[3. Inspect Repository] ──(Check existing files, schemas, and tests)
        ↓
[4. Verify Scope] ───────(Confirm alignment with V1 Scope)
        ↓
[5. Plan Smallest Diff] ─(Formulate minimal correct change)
        ↓
[6. Implement & Test] ───(Write code + unit/integration tests)
        ↓
[7. Run Quality Gates] ──(Linter, type checks, test suite)
        ↓
[8. Review Git Diff] ────(Verify zero unintended modifications)
        ↓
[9. Document & Report] ──(Update docs if required, report exact changes)
```

---

## 4. Engineering & Safety Constraints

1. **No Invented Requirements:** Do not invent features, configuration flags, or product behavior not explicitly defined in `docs/01-product/`.
2. **No Speculative Architecture:** Do not add microservices, databases, or third-party dependencies unless approved via an ADR in `docs/06-decisions/`.
3. **Minimal Diff Principle:** Make the smallest correct change that satisfies the requirement. Do not perform cosmetic refactoring on unrelated lines or files.
4. **Zero Unvalidated Code:** All runtime patch logic must be verified in isolated sandbox environments. Never assume generated code compiles.
5. **Human-in-the-Loop:** Akesis V1 never automatically merges patches into user repositories. All fixes are delivered as approval-gated Pull Requests.
6. **Type Safety & Testing:** All Python code must be 100% type-annotated (`mypy --strict`) and accompanied by tests (`pytest`).
7. **Conventional Commits:** All Git commits must follow Conventional Commits 1.0.0 format (`feat(...)`, `fix(...)`, `docs(...)`, `chore(...)`).

---

## 5. WHEN YOU MUST STOP

You must **STOP execution immediately and request human clarification** when any of the following occur:

*   **Requirement Conflict:** You detect a contradiction between two documents (e.g., `prd.md` vs `system-architecture.md`). Do not silently pick one.
*   **Scope Violation:** A requested task requires functionality outside the defined V1 scope (e.g., automated merge without human review, complex multi-file architectural refactoring).
*   **Dependency Introduction:** A change requires introducing a new external library, framework, or third-party service not listed in `docs/02-architecture/` or `docs/03-engineering/`.
*   **Security Ambiguity:** A proposed action affects secrets handling, token scoping, sandbox isolation boundaries, or untrusted code execution.
*   **Missing Architectural Decision:** A change requires a fundamental design choice that lacks an approved ADR in `docs/06-decisions/`.

---

## 6. Repository Documentation Map
*   [`docs/00-foundation/`](docs/00-foundation/) — Project Charter, Vision, Mission, Principles, Terminology.
*   [`docs/01-product/`](docs/01-product/) — PRD, Personas, Use Cases, User Flows, Requirements, Scope, Success Metrics.
*   [`docs/02-architecture/`](docs/02-architecture/) — System Architecture, Component Specs, Agent Architecture, Data Flows, Integrations, Deployment.
*   [`docs/03-engineering/`](docs/03-engineering/) — Engineering Standards, Coding Guidelines, Testing Strategy, Git Workflow, Security, DoD.
*   [`docs/04-ai/`](docs/04-ai/) — Agent Behavior, AI Engineering Rules, Prompt Standards, Model Strategy, Evaluation, Safety.
*   [`docs/05-development/`](docs/05-development/) — Development Workflow, Phase Plan, Local Setup.
*   [`docs/06-decisions/`](docs/06-decisions/) — Architecture Decision Records (ADRs).
