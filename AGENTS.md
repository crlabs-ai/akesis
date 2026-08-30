# AGENTS.md — Operational Instructions for AI Agents in Akesis

## 1. Purpose & Authority
This document is the authoritative instruction manual for all AI coding agents (including Antigravity, OpenCode, Codex, and Claude) operating within the `akesis` repository.

Akesis V1 is a complete, production-inspired vertical slice of an AI-powered CI/CD remediation platform. It is designed to be fully understandable and maintainable by a single engineer without requiring distributed enterprise infrastructure.

---

## 2. Document Authority Hierarchy
When resolving requirements, architecture, or implementation decisions, strictly follow this precedence:

1. **LEVEL 1:** Explicit decisions made by the project owner (prompts and direct instructions).
2. **LEVEL 2:** Approved product documentation (`docs/00-foundation/`, `docs/01-product/`).
3. **LEVEL 3:** Approved architecture documentation (`docs/02-architecture/`).
4. **LEVEL 4:** Approved ADRs (`docs/06-decisions/`).
5. **LEVEL 5:** Engineering standards (`docs/03-engineering/`, `docs/04-ai/`).
6. **LEVEL 6:** Existing validated implementation code.
7. **LEVEL 7:** General software engineering best practices.
8. **LEVEL 8:** AI inference.

**CRITICAL RULE:** An AI agent must **NEVER** use inference (Level 8) to override or modify an explicit project decision (Levels 1–5).

---

## 3. Mandatory Pre-Implementation Reading Rule
Before generating or modifying code, an AI agent must explicitly read the relevant authoritative documentation under `docs/`. Never rely solely on training priors or assumptions.

---

## 4. Core V1 Engineering Constraints

1. **Canonical Tooling:** The repository uses **Python 3.12+** and **`uv`** as the exclusive package and environment manager. Do not reference or introduce Poetry.
2. **Canonical Commands:**
   * `uv sync` (environment sync)
   * `uv run pytest` (test execution)
   * `uv run ruff check .` (linting)
   * `uv run mypy .` (type checking)
3. **Logging & Observability:** V1 uses **`structlog`** for structured JSON logging with correlation IDs. Do not add OpenTelemetry dependencies or distributed tracing to V1.
4. **Model Architecture:** Single LLM Provider Interface $ightarrow$ Primary Configured Model $ightarrow$ Structured Pydantic Output $ightarrow$ Application Validation. No multi-tier routing, model selection agents, or dynamic cost/latency optimizers in V1.
5. **Sandbox Isolation:** Docker-based isolated execution, non-root user, resource caps, temporary directory mount. Network is **disabled by default**, enabled explicitly only during dependency installation steps.
6. **Minimal Diff Principle:** Make the smallest correct change required. No gratuitous refactoring or unrequested formatting.
7. **Human-in-the-Loop:** All automated fixes are delivered as approval-gated Pull Requests. Zero unvalidated merges.
8. **V1 Evaluation Scope:** 10–12 carefully curated benchmark scenarios covering Lint, Dependency, and Flaky-Test Quarantine. No 200-case benchmarks in V1.

---

## 5. WHEN YOU MUST STOP

You must **STOP execution immediately and request human clarification** when:

* Requirements conflict between documents.
* Architecture conflicts are identified.
* A requested feature exceeds V1 scope boundaries.
* A new external dependency is required but not documented in standards.
* A security or isolation boundary is unclear.
* An existing architectural decision or ADR would need to be altered.
* You cannot determine the correct behavior from authoritative documentation.

**Never silently invent requirements or make unapproved architectural additions.**

---

## 6. Documentation Map
* [`docs/00-foundation/`](docs/00-foundation/) — Charter, Vision, Mission, Principles, Terminology.
* [`docs/01-product/`](docs/01-product/) — PRD, Personas, Use Cases, User Flows, Requirements, Scope, Metrics.
* [`docs/02-architecture/`](docs/02-architecture/) — System, Component, Agent, Data Flow, Integration, Deployment.
* [`docs/03-engineering/`](docs/03-engineering/) — Standards, Coding Rules, Testing, Git, Security, DoD.
* [`docs/04-ai/`](docs/04-ai/) — Agent Behavior, AI Rules, Prompts, Model Strategy, Evaluation, Safety.
* [`docs/05-development/`](docs/05-development/) — Development Workflow, Phase Plan, Local Setup.
* [`docs/06-decisions/`](docs/06-decisions/) — ADRs.
* [`docs/future-scaling.md`](docs/future-scaling.md) — Future production scaling roadmap (Not V1).
