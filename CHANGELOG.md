# Changelog

All notable changes to the **Akesis** platform are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Changed (Phase 1.7: secure mutation and pull request creation)
* **Mutation no longer clones or uses the personal access token.** `GitMutationService` applies the validated patch to a copy of a verified snapshot of the approved commit and writes blobs, tree, commit, branch and pull request through the GitHub Git Data API (`src/packages/sdk/github_git_writer.py`). Every created object is checked against locally computed object IDs.
* **Installation tokens can be scoped.** `GitHubAppAuth.installation_token` accepts `repository_ids` and `permissions`, and fails closed if GitHub reports a broader grant. The mutation token is limited to the event repository, `contents`/`pull_requests` write and `metadata` read.
* **Fork-sourced incidents are refused** before any database, network, token or filesystem work (ADR-0010 blocker 1).
* **Pull requests target the verified source branch** of the failing same-repository workflow (ADR-0010 blocker 2, owner decision). The head is the Akesis remediation branch in the same repository; a default-branch failure therefore targets the default branch, which is otherwise untouched. The source branch must exist and still point at the approved full SHA before any remote object, immediately before the branch is created, and immediately before the pull request is created. A missing, inaccessible or moved source branch aborts; nothing is rebased or substituted.
* **Safe retries:** one mutation record per proposal and commit; an existing branch or open PR is reused only if it is exactly this remediation, otherwise the mutation stops. Branches are never moved or forced, and Akesis never merges.
* **Removed:** the shared-checkout clone and push mutation path, `copy_checkout_without_links`, and the unused `GitHubClient.create_pull_request` and `find_pull_request` methods. The remediation hold (ADR-0010) is unchanged.

### Added
* **Durable webhook delivery records** (migrations `0005_create_webhook_events`, `0006_webhook_retry_schedule`, `0007_worker_visibility`): deliveries are persisted and claimed atomically, with stale-write protection by attempt count.
* **Webhook retry policy:** at most `webhook_retry_max_attempts` (default 5) verification attempts per delivery. Computed backoff starts at 60 seconds, doubles with jitter, and is capped at 15 minutes. Provider `Retry-After` and rate-limit reset delays are honoured in full.
* **Provider delay ceiling:** a provider delay above `webhook_retry_provider_delay_ceiling_seconds` (default 24 hours) becomes an operator-visible `provider_hold`, released when due. It is never silently shortened.
* **Terminal `exhausted` state** with failure code `verification_retries_exhausted`. Exhausted deliveries return HTTP 422 on every redelivery, and GitHub is not called again. `last_error_code` keeps the most recent transient cause.
* **Retry worker** (`python -m src.apps.worker`): executes due retries. It claims with `FOR UPDATE SKIP LOCKED` and the same atomic transitions, rebuilds verification input only from persisted identity, backs off during database outages, and finishes claimed work on SIGTERM. Readiness is reported through a heartbeat file and `python -m src.apps.worker.healthcheck`.
* **Operator endpoint** `GET /v1/webhook-deliveries`: read-only, authenticated with the read API token, keyset-paginated on `(updated_at, delivery_id)`, with an explicit ten-field response allow-list.
* **`Retry-After` response header** on refused early redeliveries, with a detail message that distinguishes a scheduled retry from a verification already in progress.

### Added (verification lease)
* **Verification deadline:** `webhook_verification_deadline_seconds` (default 240, maximum 540) bounds the total time of one provenance verification in both the route and the worker. A verification past the deadline is a retryable `verification_deadline_exceeded` failure. Measured worst case without the bound was about 105 sequential GitHub requests, roughly 50 minutes, against a 10-minute stale-claim window.
* **Multi-process tests:** two worker processes share one database, with no duplicate GitHub verification. Also covered: a SIGKILLed worker's claim is recovered only after the stale window, SIGTERM during a verification completes it, and restarts resume durable work.

### Changed (Phase 15: shutdown and security review)
* **Graceful shutdown releases unstarted claims.** On SIGTERM the worker starts no further delivery and returns every claim it has not started to the schedule, without consuming an attempt. A delivery already being verified completes. If the database is unavailable during release, the claim is left to the stale-claim window.
* **Watchdog support.** The worker sends systemd `WATCHDOG=1` pings only when a delivery or a pass completes, never from a timer. A frozen worker is restarted by the watchdog. Verified under user-scope systemd.
* **Heartbeat window** default raised from 60 to 300 seconds, so one healthy verification of up to the 240-second deadline does not appear stale.
* **Gate ordering.** The remediation hold now runs before any provenance database read in `process_failure`, `resume_approval` and `create_pull_request`.
* **Sandbox** adds `--pids-limit 256`.

### Removed
* `_safe_process_failure`, an unused background path that was the only route from the webhook to `process_failure`. The committed `HEAD` still schedules it. Do not deploy `HEAD`.

### Changed
* The post-verification path is shared between the webhook route and the worker (`webhook_admission.py`). It still never runs remediation.
* `webhook_retry_max_delay_seconds` default lowered from 3600 to 900.
* The worker heartbeat default moved from `/var/tmp` to `/run/akesis`. Override with `WORKER_HEARTBEAT_PATH` if needed.

### Fixed
* A non-finite `x-ratelimit-reset` value (for example `nan`) produced an immediate retry. Non-finite provider values are now rejected.
* Retry scheduling was persisted as a column but never written, so transient failures were immediately re-claimable. Scheduling is now enforced at the claim boundary.
* Worker attempt numbering double-counted an attempt when the claim synchronised the ORM object.
* The worker heartbeat wrote through a predictable temporary name under `/var/tmp`. A local user could redirect that write to another file owned by the service. The heartbeat now uses a random temporary file created in a service-owned runtime directory (default `/run/akesis/worker-heartbeat.json`), and a regression test covers the planted-symlink case. The attempt number is now captured before the claim. This was caught by the worker tests before release.

### Known limitations
* V1 sign-off still depends on the live integration smoke test, which is operator-run.
* Operator requeue is deliberately not implemented. Recovery is a re-run of the workflow in GitHub.
* The unconditional remediation hold (ADR-0010) remains in effect.
* The private key path is verified with the real settings loader and under a user-scope systemd unit (quoted multi-line `.env`). The system-level `akesis` account and `/run` ownership have not been verified, because this environment has no root access.
* A worker that is alive but waiting out a database outage is not restarted by the watchdog, because it keeps pinging. Its state is visible through the probe.
* Shutdown finishes at most the delivery in flight, bounded by the 240-second deadline, so `TimeoutStopSec=300s` covers it. Claims not yet started are released.

---

## [0.1.0] - 2026-09-01

### Added
* **Phase 0 (Project Foundation):** Established specification-driven documentation baseline, Python 3.12+ runtime, and `uv` package manager (ADR-0001).
* **Phase 1 (Webhook Ingestion & Parsing):** Implemented FastAPI Webhook Gateway (`/v1/webhooks/github`) with HMAC-SHA256 signature verification and regex-based ANSI stripping/log signal extraction (`FailureSignal`, `FailureContext`).
* **Phase 2 (Gemini Structured Diagnosis):** Implemented provider-agnostic `LLMClientProtocol` and `GeminiClient` with schema-enforced Pydantic generation (`DiagnosisProposal`, `DiagnosticResult`) and 0.0 confidence fallback (ADR-0002).
* **Phase 3 (Codebase Context Retrieval):** Implemented `GitRepositoryCheckoutManager` for exact commit SHA checkout and `CodebaseContextResolver` with bounded 40-line windowing and path traversal security (ADR-0003).
* **Phase 4 (Fix Proposal Engine):** Implemented `FixProposalService` and `PatchValidator` with unified diff parsing (`FilePatch`, `PatchHunk`), strict budget caps (max 2 files / 100 lines / 4,000 characters), protected file shielding, and evidence grounding (ADR-0004).
* **Phase 5 (Isolated Docker Sandbox Validation):** Implemented `DockerSandboxRunner` and `ValidationService` executing ephemeral container verifications with `--network none`, `--cap-drop ALL`, non-root user `akesis` (`uid 1000`), and verbatim `git apply` (ADR-0005).
* **Phase 6 (Human Approval Gate & Persistence):** Implemented `ApprovalService`, `SlackClient` Block Kit card dispatch, `/v1/slack/interactions` callback endpoint, and async PostgreSQL persistence for table `approvals` with atomic state transitions (ADR-0006).
* **Phase 7 (Controlled Git Mutation & PR Delivery):** Implemented `GitMutationService` and `GitHubClient` with preflight invariant verification, stale commit SHA aborts, deterministic branch naming (`akesis/fix/...`), pre-push Docker validation, GitHub PR opening, and PostgreSQL table `mutations` (ADR-0007).
* **Modernized Documentation Suite:** Added comprehensive High-Level Design (`docs/architecture/HLD.md`), Low-Level Design (`docs/architecture/LLD.md`), REST API Reference (`docs/api/README.md`), Deployment Guide (`docs/deployment/README.md`), Operations Runbook (`docs/operations/runbook.md`), and updated root `README.md`.
