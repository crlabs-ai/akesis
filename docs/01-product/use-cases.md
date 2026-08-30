# Core Use Cases: Akesis V1

---

## UC-01: Automated Lint & Code-Formatting Remediation
* **Actor:** Software Developer / GitHub Actions Workflow
* **Trigger:** GitHub Actions emits a `workflow_run` failure event caused by a linter or formatter (e.g., ESLint, Ruff, Prettier, Black).
* **Preconditions:** Akesis GitHub App is installed on the repository.
* **Main Flow:**
    1. Akesis receives webhook payload with status `failure`.
    2. Ingestion service streams logs and isolates the lint rule violation.
    3. Agent extracts the target file and line ranges.
    4. Remediation engine synthesizes a minimal diff resolving the violation.
    5. Docker sandbox executes the exact linter command with the patch applied (network disabled).
    6. Sandbox reports: Exit code 0, 0 errors, 0 warnings.
    7. Delivery service opens a Pull Request to the feature branch.
    8. Developer reviews diff and merges PR.
* **Failure Condition:** Sandbox validation fails after bounded retry. Incident is logged as `FAILED_VALIDATION`, and no PR is created.

---

## UC-02: Broken Dependency & Lockfile Resolution
* **Actor:** Software Developer / CI Pipeline
* **Trigger:** CI build fails during dependency installation due to missing package or out-of-sync lockfile.
* **Preconditions:** Manifest file (`package.json`, `pyproject.toml`) and lockfile exist.
* **Main Flow:**
    1. Akesis ingests build failure log and isolates the dependency resolution error.
    2. Agent identifies the missing package or lockfile discrepancy.
    3. Sandbox enables controlled network access and executes dependency resolution (`uv lock`, `npm install --package-lock-only`).
    4. Sandbox disables network access and verifies project compiles cleanly.
    5. Akesis creates a PR containing the updated lockfile with an explanation.
* **Failure Condition:** Dependency requires breaking major version code changes. Flagged for manual review.

---

## UC-03: Flaky-Test Identification & Quarantine
* **Actor:** Quality Assurance / Developer
* **Trigger:** A test fails sporadically on a PR that contains zero changes to the underlying module.
* **Preconditions:** Test suite is executed via standard runners (pytest, jest).
* **Main Flow:**
    1. Akesis identifies a failed test assertion.
    2. Sandbox executes the failing test up to 10 times in isolation.
    3. If the test passes in $>30\%$ of isolated runs without code changes, Akesis classifies it as `FLAKY_TEST`.
    4. Akesis opens a PR adding a `@pytest.mark.flaky` or `@quarantine` annotation with historical failure telemetry.
    5. Main pipeline is unblocked while the issue is routed for investigation.
