# Product Scope Specification: Akesis

This document defines the strict boundaries for Akesis V1, Planned Future Phases, and Explicit Non-Goals.

---

## 1. In-Scope for V1 (The Commitments)
* **Architecture:** Modular single-service control plane maintainable by a single engineer.
* **Persistence:** PostgreSQL 16 for durable approval and mutation state tracking.
* **Tooling Standard:** **Python 3.12+** and **`uv`**.
* **Observability:** **`structlog`** with correlation IDs.
* **Supported Platform:** GitHub Actions workflows on public and private repositories.
* **Supported Languages / Ecosystems:**
    * Python (Ruff, pytest, mypy, uv)
* **Supported Remediation Domains:**
    1. **Lint & Code Quality Violations:** (Ruff, formatting, syntax).
    2. **Dependency & Import Collisions:** (missing package imports, syntax failures).
    3. **Test Assertion Failures:** (pytest unit test regressions).
* **Execution & Validation Model:** Ephemeral Docker container sandbox (`akesis-validator:v1`, `--network none`, `--cap-drop ALL`, verbatim `git apply`).
* **Human-in-the-Loop Approval:** Mandatory human authorization via interactive Slack cards; durable approval state transitions in PostgreSQL.
* **Delivery Model:** Controlled Git mutation, deterministic fix branch creation, and GitHub Pull Request with rich audit context.
* **Evaluation Baseline:** 10–12 curated representative benchmark scenarios.

---

## 2. Planned for Future Phases (Documented in docs/future-scaling.md)
* Autonomous auto-merge on verified low-risk fixes.
* Multi-tier model routing and dynamic cost/latency optimizers.
* OpenTelemetry distributed tracing and metrics.
* Asynchronous distributed task queues (Redis/Celery/Kafka) and worker pools.
* Pre-warmed container pools and Kubernetes sandbox workers (EKS/GKE).
* 200+ case golden benchmark suites.
* Multi-file architectural bug repairs and multi-repository sync.
* Integrations with GitLab CI/CD, CircleCI, Bitbucket Pipelines, and Jenkins.

---

## 3. Explicit Non-Goals (What Akesis Is NOT in V1)
* **NOT an Autonomous Bot that merges to main:** Never merges to base branches; all PRs require human review.
* **NOT an IDE Autocomplete Extension:** Operates at the CI pipeline stage, not in-editor typing.
* **NOT a Chatbot Interface:** Does not offer free-form conversational chat without pipeline failure context.
* **NOT an Arbitrary Shell Executor:** Does not allow arbitrary command execution outside the allowlisted validation commands.
