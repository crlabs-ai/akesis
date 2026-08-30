# Product Requirements Specification: Akesis V1

All requirements are assigned a permanent unique identifier and categorized as Functional (FR) or Non-Functional (NFR).

---

## 1. Functional Requirements (FR)

| Requirement ID | Priority | Category | Specification | Verification Method |
| :--- | :--- | :--- | :--- | :--- |
| **FR-001** | P0 | Ingestion | System must ingest GitHub `workflow_run` webhooks over HTTPS TLS 1.3 and verify HMAC-SHA256 signatures. | Automated security contract test. |
| **FR-002** | P0 | Ingestion | System must download and buffer job logs up to 50MB in size without memory exhaustion. | Load test with oversized log streams. |
| **FR-003** | P0 | Log Parsing | System must strip ANSI codes and extract error stack traces using regex + model heuristics. | Unit tests across 100 benchmark log samples. |
| **FR-004** | P0 | Diagnosis | System must classify failures into `LINT`, `DEPENDENCY`, `FLAKY_TEST`, or `UNSUPPORTED`. | Classification evaluation on golden dataset. |
| **FR-005** | P0 | Patch Gen | System must synthesize standard unified git diffs targeting only the files and lines causing the failure. | Diff AST analysis; assert zero extraneous edits. |
| **FR-006** | P0 | Sandbox | System must execute patches in ephemeral, isolated Docker containers with CPU/memory limits. | Integration test verifying container lifecycle. |
| **FR-007** | P0 | Validation | System must assert that the sandbox verification command returns exit code 0 before initiating delivery. | Automated test with deliberately broken patches. |
| **FR-008** | P0 | Delivery | System must open a GitHub Pull Request against the feature branch using an authorized GitHub App installation token. | End-to-end integration test with mock GitHub API. |
| **FR-009** | P1 | Audit | System must log every incident transition, prompt hash, confidence score, and sandbox output into PostgreSQL. | Database audit table verification. |
| **FR-010** | P1 | Quarantine | System must flag and quarantine flaky tests after proving $>30\%$ non-deterministic pass rate in isolated runs. | Mock flaky test execution suite. |

---

## 2. Non-Functional Requirements (NFR)

| Requirement ID | Priority | Category | Specification | Target Metric |
| :--- | :--- | :--- | :--- | :--- |
| **NFR-001** | P0 | Latency | End-to-end execution time from webhook ingestion to PR delivery. | $p95 < 180\text{s}$, $p50 < 60\text{s}$. |
| **NFR-002** | P0 | Reliability | Ingestion API gateway availability. | $99.9\%$ uptime SLA. |
| **NFR-003** | P0 | Security | Sandbox container process privilege level. | Non-root (`uid 1000`), dropped `ALL` capabilities, `no-new-privileges`. |
| **NFR-004** | P0 | Data Privacy | Permanent retention of customer proprietary source code. | Zero customer code saved in permanent DB storage. |
| **NFR-005** | P1 | Concurrency | Simultaneous active remediation incidents supported. | $\ge 50$ concurrent sandboxes per worker cluster. |
| **NFR-006** | P1 | Maintainability | Codebase test coverage across all domain modules. | $\ge 85\%$ line coverage via `pytest-cov`. |
| **NFR-007** | P1 | Observability | Distributed trace propagation across HTTP, gRPC, and queue workers. | 100% traces carry OpenTelemetry `traceparent`. |
