# ADR-0008: End-to-End Remediation Pipeline Orchestrator

## Status
Accepted

## Date
2026-09-01

## Context
Akesis Phases 1 through 7 implemented isolated components for webhook ingestion, LLM failure diagnosis, codebase context extraction, fix proposal generation, Docker sandbox validation, human-in-the-loop approval, and controlled Git mutation. 

Phase 8 connects these discrete services into a unified, non-blocking asynchronous pipeline (`RemediationOrchestrator`) while preserving critical safety boundaries:
1. **Non-Blocking Ingestion**: The GitHub webhook gateway must acknowledge events with HTTP 202 without holding client connections open during multi-second diagnosis and validation.
2. **Durable Pause & Resume**: The pipeline must pause in an `awaiting_approval` state, dispatch Slack interactive cards, and resume via `/v1/slack/interactions` callback without keeping long-lived in-memory coroutines or worker threads alive.
3. **Strict Human Authorization**: Git mutation and Pull Request creation cannot execute without authoritative approval recorded in PostgreSQL.

## Decision
1. **Pipeline Domain & State Machine**: We implement `PipelineStatus` (`received`, `diagnosing`, `proposing`, `validating`, `awaiting_approval`, `approved`, `rejected`, `mutating`, `completed`, `failed`) and `PipelineRecord` domain models, persisted via `PipelineModel` in PostgreSQL table `pipelines`.
2. **Remediation Orchestrator**: We implement `RemediationOrchestrator` in `src/packages/shared/remediation_orchestrator.py` coordinating the lifecycle without duplicating domain logic.
3. **Execution Model**: We use FastAPI `BackgroundTasks` for asynchronous dispatch, ensuring fast HTTP responses and decoupling ingress from execution without adding external message queues (Redis/Celery).

## Consequences
### Positive
* Complete vertical slice integration from webhook failure detection to Pull Request delivery.
* Durable state transitions tracked at each stage in PostgreSQL.
* Zero unverified or autonomous code pushes.

### Negative / Limitations
* In-memory background task execution requires the API process to remain active during stage execution; distributed task persistence across multiple worker nodes is deferred to future enterprise scaling.
