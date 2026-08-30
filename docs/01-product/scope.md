# Product Scope Specification: Akesis

This document defines the strict boundaries for Akesis V1, Planned Future Phases, and Explicit Non-Goals.

---

## 1. In-Scope for V1 (The Commitments)
* **Architecture:** Standalone, production-inspired vertical slice maintainable by a single engineer.
* **Tooling Standard:** **Python 3.12+** and **`uv`**.
* **Observability:** **`structlog`** with correlation IDs.
* **Supported Platform:** GitHub Actions workflows on public and private repositories.
* **Supported Languages / Ecosystems:**
    * Python (Ruff, Black, Flake8, pytest, uv)
    * JavaScript / TypeScript (ESLint, Prettier, Jest, npm)
    * Go (gofmt, golangci-lint, go test, go mod)
* **Supported Remediation Domains:**
    1. Linting & formatting violations.
    2. Dependency lockfile resolution and missing package imports.
    3. Flaky-test identification, repeated isolation testing, and quarantine PR tagging.
* **Execution Model:** Ephemeral Docker container sandbox (network disabled by default).
* **Delivery Model:** GitHub Pull Request with structured diagnostic context.
* **Evaluation Baseline:** 10–12 curated representative benchmark scenarios.

---

## 2. Planned for Future Phases (Documented in docs/future-scaling.md)
* Multi-tier model routing, dynamic cost/latency optimizers, and model-selection agents.
* OpenTelemetry distributed tracing and metrics.
* Asynchronous task queues (Redis/ARQ) and distributed worker pools.
* Pre-warmed container pools and Kubernetes sandbox workers (EKS/GKE).
* 200+ case golden benchmark suites.
* Advanced outbound network proxy isolation.
* Multi-file architectural bug repairs and runtime logic exception fixing.
* Integrations with GitLab CI/CD, CircleCI, Bitbucket Pipelines, and Jenkins.

---

## 3. Explicit Non-Goals (What Akesis Is NOT)
* **NOT an IDE Autocomplete Extension:** Operates at the CI pipeline stage, not in-editor typing.
* **NOT a Chatbot Interface:** Does not offer free-form conversational chat without pipeline context.
* **NOT an Unattended Auto-Merger:** Akesis V1 will never push or merge code directly to `main` without human review.
