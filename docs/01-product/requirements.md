# Product Requirements Specification: Akesis V1

All requirements are assigned a permanent unique identifier and categorized as Functional (FR) or Non-Functional (NFR).

---

## 1. Functional Requirements (FR)

| Requirement ID | Priority | Category | Specification | Verification Method |
| :--- | :--- | :--- | :--- | :--- |
| **FR-001** | P0 | Ingestion | System must ingest GitHub `workflow_run` webhooks over HTTPS TLS 1.3 and verify HMAC-SHA256 signatures. | Automated contract test. |
| **FR-002** | P0 | Log Parsing | System must strip ANSI codes and extract error stack traces using structured log signal extractors. | Unit tests across representative log samples. |
| **FR-003** | P0 | Diagnosis | System must classify failures into `LINT`, `DEPENDENCY`, `FLAKY_TEST`, or `UNSUPPORTED`. | Classification tests on 10–12 benchmark cases. |
| **FR-004** | P0 | Patch Gen | System must synthesize standard unified git diffs targeting only the files and lines causing the failure. | Diff AST analysis; assert zero extraneous edits. |
| **FR-005** | P0 | Sandbox | System must execute patches in ephemeral Docker containers with non-root user and resource limits. | Integration test verifying container lifecycle. |
| **FR-006** | P0 | Network Policy | Sandbox network access must be disabled by default, enabled explicitly only for dependency installation steps. | Network isolation integration test. |
| **FR-007** | P0 | Validation | System must assert that the sandbox verification command returns exit code 0 before initiating delivery. | Automated test with deliberately broken patches. |
| **FR-008** | P0 | Delivery | System must open a GitHub Pull Request against the feature branch using an authorized GitHub App installation token. | End-to-end integration test with mock GitHub API. |
| **FR-009** | P1 | Audit Logging | System must log every incident transition, correlation ID, confidence score, and sandbox output via `structlog`. | Structured log verification test. |
| **FR-010** | P1 | Quarantine | System must flag and quarantine flaky tests after proving non-deterministic pass rate in isolated runs. | Mock flaky test execution suite. |

---

## 2. Non-Functional Requirements (NFR)

| Requirement ID | Priority | Category | Specification | Target Metric |
| :--- | :--- | :--- | :--- | :--- |
| **NFR-001** | P0 | Latency | End-to-end execution time from webhook ingestion to PR delivery. | $< 180\text{s}$ for 95% of V1 failure classes. |
| **NFR-002** | P0 | Reliability | Ingestion API gateway availability. | $99.9\%$ uptime. |
| **NFR-003** | P0 | Security | Sandbox container process privilege level. | Non-root (`uid 1000`), resource memory limits (512MB). |
| **NFR-004** | P0 | Data Privacy | Permanent retention of customer proprietary source code. | Zero customer code saved in permanent DB storage. |
| **NFR-005** | P1 | Maintainability | Codebase test coverage across all domain modules. | $\ge 85\%$ line coverage via `pytest-cov`. |
| **NFR-006** | P1 | Tooling Standard | Canonical Python runtime and dependency manager. | **Python 3.12+** and **`uv`**. |
