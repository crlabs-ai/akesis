# Testing Strategy: Akesis

This document outlines the testing pyramids, evaluation harnesses, and coverage requirements.

---

## 1. Testing Pyramid

```text
        / \
       / E2E \         (10% - Full GitHub Webhook to Mock PR verification)
      /-------\
     / Integration\   (30% - Postgres, Redis, Docker Sandbox testcontainers)
    /-------------\
   /   Unit Tests  \  (60% - Fast, deterministic pure functions & AST parsing)
  /-----------------\
```

---

## 2. Test Suites & Execution
*   **Unit Tests (`tests/unit/`):** Test pure business logic, AST parsing, and prompt schema serialization. Target execution speed: $< 5$ seconds for full suite.
*   **Integration Tests (`tests/integration/`):** Test database persistence, Redis queue consumers, and real Docker container sandbox runs using `testcontainers-python`.
*   **Mocking Policy:** All external HTTP calls to GitHub API and LLM providers must be mocked using `respx` or `pytest-mock`. Real API keys must never be used in standard CI test suites.
*   **Code Coverage Baseline:** Minimum **85% statement coverage** enforced by `pytest-cov` in CI quality gates.
