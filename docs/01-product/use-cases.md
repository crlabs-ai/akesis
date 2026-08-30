# Core Use Cases: Akesis V1

---

## UC-01: Automated Lint & Code-Formatting Remediation
*   **Actor:** Software Developer / GitHub Actions Workflow
*   **Trigger:** GitHub Actions emits a `workflow_run` failure event caused by a linter or formatter (e.g., ESLint, Ruff, Prettier, Black).
*   **Preconditions:**
    1. Akesis GitHub App is installed on the repository.
    2. Repository has an approved configuration file (`.akesis.yml`).
*   **Main Success Flow:**
    1. Akesis receives webhook payload with status `failure`.
    2. Ingestion service streams logs and identifies a lint rule failure (e.g., `unused-import`, `formatting-error`).
    3. Agent parses the target file and line ranges from the log.
    4. Remediation engine generates a minimal diff resolving the violation.
    5. Sandbox engine runs the exact linter command inside an ephemeral container with the patch applied.
    6. Sandbox reports: Exit code 0, 0 errors, 0 warnings.
    7. Delivery engine opens a Pull Request to the feature branch titled `fix(lint): resolve ruff/eslint violations`.
    8. Developer reviews diff and merges PR.
*   **Alternative Flow (Low Confidence):**
    *   If the linter violation is ambiguous (e.g., complex type refactor), agent confidence is $< 0.8$. Akesis logs diagnosis and creates an issue/comment instead of a speculative PR.
*   **Failure Condition:** Sandbox validation fails after 2 patch iterations. Incident is marked `FAILED_VALIDATION`, logged, and no PR is created.

---

## UC-02: Broken Dependency & Lockfile Collision Resolution
*   **Actor:** Software Developer / CI Pipeline
*   **Trigger:** CI build fails during `npm install`, `poetry install`, or `go mod download` due to package checksum mismatch, missing peer dependency, or out-of-sync lockfile.
*   **Preconditions:** Manifest file (`package.json`, `pyproject.toml`) and lockfile exist in repository.
*   **Main Success Flow:**
    1. Akesis ingests build failure log and isolates the dependency resolution error.
    2. Agent identifies the missing dependency or lockfile discrepancy.
    3. Sandbox environment loads manifest and executes the package manager's clean resolution command (e.g., `npm install --package-lock-only`).
    4. Sandbox verifies project builds cleanly.
    5. Akesis creates a PR containing the updated lockfile with an explanation of the package resolution.
*   **Failure Condition:** Dependency requires major version migration with breaking API changes. Akesis marks as out of scope for V1 and flags for manual review.

---

## UC-03: Flaky-Test Identification & Quarantine
*   **Actor:** Quality Assurance / Developer
*   **Trigger:** A test fails on a PR that contains zero changes to the underlying tested module.
*   **Preconditions:** Test suite is executed via standard runners (pytest, jest, go test).
*   **Main Success Flow:**
    1. Akesis identifies a failed test assertion.
    2. Ingestion service checks historical failure database and detects non-deterministic failure pattern.
    3. Sandbox spins up and executes the failing test 10 times in isolation with varying seed/timing parameters.
    4. If the test passes in 6/10 runs without code changes, Akesis confirms `FLAKY_TEST` classification.
    5. Akesis generates a PR adding a `@pytest.mark.flaky` or `@quarantine` annotation with historical failure telemetry.
    6. Main pipeline is unblocked while the issue is routed to the owning squad.
