# Product Scope Specification: Akesis

This document defines the strict boundaries for Akesis V1, Planned Future Phases, and Explicit Non-Goals.

---

## 1. In-Scope for V1 (The Commitments)
*   **Supported Platform:** GitHub Actions workflows on public and private repositories.
*   **Supported Languages / Ecosystems:**
    *   Python (Ruff, Black, Flake8, pytest, Poetry, pip)
    *   JavaScript / TypeScript (ESLint, Prettier, Jest, npm, yarn)
    *   Go (gofmt, golangci-lint, go test, go mod)
*   **Supported Remediation Domains:**
    1.  Linting & formatting violations.
    2.  Dependency lockfile resolution and missing package imports.
    3.  Flaky-test identification, repeated isolation testing, and quarantine PR tagging.
*   **Execution Model:** Ephemeral Docker container sandbox validation.
*   **Delivery Model:** GitHub Pull Request with structured diagnostic context.

---

## 2. Planned for Future Phases (Post-V1)
*   **Phase 2:** Multi-file architectural bug repairs and runtime logic exception fixing.
*   **Phase 2:** CI integrations with GitLab CI/CD, CircleCI, Bitbucket Pipelines, and Jenkins.
*   **Phase 3:** Autonomous dependency version upgrades with breaking change migration.
*   **Phase 3:** APM integration (Datadog/Sentry) for automated staging/production bug triage.
*   **Phase 4:** Optional automated merge policies for verified non-breaking changes based on repository trust rules.

---

## 3. Explicit Non-Goals (What Akesis Is NOT)
*   **NOT an IDE Autocomplete Extension:** Akesis is not a competitor to Copilot/Cursor typing completions; it operates on the CI server.
*   **NOT a Chatbot Interface:** Akesis does not offer a free-form conversational interface without pipeline context.
*   **NOT an Autonomous Codebase Rewriter:** Akesis will never perform unrequested large-scale repository refactorings.
*   **NOT an Unattended Auto-Merger (in V1):** Akesis will never push or merge code directly to `main` without human approval.
