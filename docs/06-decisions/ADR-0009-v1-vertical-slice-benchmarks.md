# ADR-0009: V1 Vertical Slice Validation & Release Benchmark Baseline

## Status
Accepted

## Date
2026-09-01

## Context
Akesis V1 requires an authoritative, repeatable, and automated vertical-slice benchmark test harness to validate end-to-end reliability, security invariants, and fail-closed safety behaviors prior to release sign-off.

The system must prove deterministic execution across the entire pipeline lifecycle without contacting external live production services (GitHub, Slack, live LLM APIs) during automated benchmark runs.

## Decision
1. **Benchmark Suite**: We implement a 12-scenario vertical slice benchmark suite in `tests/benchmark/test_vertical_slice_benchmarks.py` covering:
   - **Scenario A**: Ruff/lint remediation.
   - **Scenario B**: Pytest unit test assertion remediation.
   - **Scenario C**: Missing dependency / `pyproject.toml` configuration remediation.
   - **Scenario D**: Low-confidence diagnosis rejection.
   - **Scenario E**: Malformed/invalid fix proposal rejection.
   - **Scenario F**: Protected path / arbitrary target path rejection.
   - **Scenario G**: Sandbox validation failure halting pipeline.
   - **Scenario H**: Human rejection in Slack terminating mutation.
   - **Scenario I**: Approval expiry preventing mutation.
   - **Scenario J**: Stale commit SHA aborting mutation prior to branch creation.
   - **Scenario K**: Duplicate webhook delivery idempotency.
   - **Scenario L**: End-to-end approved mutation and Pull Request delivery with full audit trail.
2. **Invariants Proven**:
   - Zero autonomous mutation without human approval in PostgreSQL.
   - Exact commit SHA and proposal ID binding across pipeline, approval, and mutation records.
   - Verbatim patch preservation from synthesis to GitHub PR.
   - Clean failure isolation without process crashing or unhandled background exceptions.

## Consequences
### Positive
* Deterministic, 100% reproducible vertical-slice regression harness.
* Full test coverage across all 9 phases exceeding 90%.
* Complete verification of safety boundaries and release readiness.

### Negative / Limitations
* Integration benchmarks use mocked external network boundaries (Slack/GitHub APIs); live sandbox execution requires local Docker daemon and PostgreSQL service.
