# Product Requirements Document (PRD): Akesis V1

```text
Document ID: PRD-001
Status: Approved V1 Baseline
Product Owner: CRLabs AI Product Leadership
Target Release: V1.0.0 (Vertical Slice)
```

---

## 1. Product Overview
Akesis V1 is an intelligent CI/CD remediation platform that integrates with GitHub Actions to detect pipeline failures, extract root causes, synthesize minimal code patches, validate fixes in Docker sandboxes, and deliver reviewable Pull Requests.

---

## 2. Problem Statement & Jobs-to-be-Done
* **Problem:** Developers spend ~20% of engineering bandwidth manually triaging repetitive lint errors, lockfile collisions, and investigating flaky tests.
* **Job-to-be-Done:** *"When my CI pipeline fails on a feature branch, I want an immediate, verified fix delivered as a PR so that my pipeline turns green without me losing focus on my core task."*

---

## 3. Target Personas
1. **Backend Engineer:** Needs fast resolution for broken builds and lint gates during feature development.
2. **DevOps / Platform Engineer:** Needs to reduce CI queue congestion and eliminate repetitive developer triage tickets.
3. **Startup Founder / Solo Developer:** Needs automated infrastructure hygiene without dedicated DevOps personnel.
4. **Engineering Manager:** Needs higher sprint velocity and predictable MTTR metrics.

---

## 4. V1 Capabilities (Strict Boundary)
Akesis V1 strictly supports three remediation categories:
1. **Lint & Code Formatting Remediation:** Automatic detection and correction of style, linter, and formatting violations (e.g., ESLint, Ruff, Black, Prettier).
2. **Dependency & Lockfile Resolution:** Automatic resolution of missing packages, version collisions, and out-of-sync lockfiles (e.g., `package-lock.json`, `uv.lock`, `go.sum`).
3. **Flaky-Test Identification & Quarantine:** Detection of non-deterministic test failures via repeated isolated execution, with automated PR generation to quarantine or annotate the test.

---

## 5. Functional Requirements Summary
* **FR-001 (Webhook Ingestion):** Ingest GitHub `workflow_run` failed events securely via HMAC-SHA256 signature verification.
* **FR-002 (Log Extraction):** Parse raw logs, strip ANSI escape sequences, and extract error blocks in under 10 seconds.
* **FR-003 (Root Cause Diagnosis):** Structure diagnosis into Pydantic JSON containing error class, file targets, line ranges, and confidence score.
* **FR-004 (Patch Synthesis):** Generate minimal unified diffs adhering to the Minimal Diff Principle.
* **FR-005 (Sandbox Validation):** Apply patch inside isolated Docker container; execute verification command; assert 0 exit code and 0 compiler warnings.
* **FR-006 (PR Delivery):** Submit Pull Request against the target branch with reproduction proof and trace context.
* **FR-007 (Audit Logging):** Persist structured logs with correlation IDs and validation outputs.

---

## 6. Non-Functional Requirements Summary
* **NFR-001 (Latency):** Total remediation lifecycle (Ingestion to PR) completes in $< 180$ seconds for standard V1 failure classes.
* **NFR-002 (Sandbox Isolation):** Sandboxes run as non-root users (`uid 1000`), with resource limits, temporary directories, and network disabled by default (enabled explicitly only for dependency installation).
* **NFR-003 (Validation Accuracy):** 100% of delivered PRs must pass sandbox validation. Zero unvalidated patches.
* **NFR-004 (Observability):** Structured logging via `structlog` with correlation IDs.

---

## 7. Out of Scope for V1
* Automatic merging of Pull Requests without human review.
* Multi-tier model routing, dynamic model selection, or cost/latency optimizers.
* Container pools, pre-warmed workers, Kubernetes clusters, or proxy infrastructure.
* Support for CI platforms other than GitHub Actions (e.g., GitLab, CircleCI, Jenkins).
* APM live production runtime error patching.
