# ADR-0010: Temporary Unconditional Remediation Hold

## Status
Accepted (temporary). Supersede this ADR when every removal condition below is met.

## Date
2026-11-12

## Context
Akesis remediation runs on repository content: exact-SHA checkout, codebase context extraction,
sandbox patch validation, and GitHub branch and PR mutation. Those operations are only safe when
the repository identity, commit SHA, branch, and workflow run they act on are independently
verified against GitHub, and when repository content is read from a private snapshot rather than
a shared or attacker-influenced checkout.

The verified-provenance path is partially built but not yet a complete trust boundary:
- `WorkflowRunProvenanceVerifier` checks signed webhook claims against the installation-scoped
  GitHub API, but its result is not yet the only admissible input to the remediation path.
- Historical pipelines created before provenance existed can lack verified provenance.
- Checkout and sandbox still read repository content without a private snapshot boundary.
- Webhook retry scheduling is enforced at the claim boundary. A separate retry worker executes
  due retries, and provider delays beyond the sanity ceiling are held visibly rather than
  shortened. Verification of a retried delivery still ends at the verified pipeline record, with
  remediation disabled.

Shipping remediation before these are closed would let unverified repository content reach
diagnosis, patch generation, sandbox execution, and PR creation.

## Decision
1. A single unconditional hold, `require_verified_provenance()` in
   `src/packages/shared/provenance.py`, raises `UnverifiedProvenanceError` unconditionally.
2. The hold is enforced at every production-facing remediation entry point, before any side effect:
   - `RemediationOrchestrator.process_failure` and `resume_approval`
   - `FixProposalService.generate_fix_proposal` (LLM call)
   - `ValidationService.validate_fix` (snapshot and sandbox)
   - `CodebaseContextResolver.resolve_context` (snapshot)
   - `ApprovalService.request_approval` (persistence and Slack card)
   - `GitMutationService.create_pull_request` (remote Git objects, branch, PR)
   - `GitRepositoryCheckoutManager.checkout_commit` (direct SDK clone or fetch; removed in Phase 1.8)
   - `DockerSandboxRunner.run_validation` (direct sandbox execution)
3. The hold is not data-dependent. Presence of a `provenance` object on a `FailureContext` does
   not lift it. There is no environment variable, configuration flag, or public parameter that
   disables it.
4. Business logic is tested through private `_..._unchecked` bodies. Each gated public method is
   `require_verified_provenance()` followed by a call to its private body. Tests call those bodies
   with explicit fixtures and controlled collaborator doubles. Production code never calls them
   from outside the class. Collaborators are still called through their gated public methods, so
   every boundary re-checks.
5. The private sandbox helper `_materialize_workspace` carries no gate. It is reachable only
   through the gated `run_validation`.

## Why temporary
The hold trades remediation availability for a closed trust boundary. It is containment, not a
design goal. The owner has stated it must not ship as permanent V1 behaviour.

## Why it must remain until the conditions are met
Removing it before verified provenance and private snapshots exist would allow repository content
from unverified events, forks, or historical records to reach code execution and PR creation.
Making tests pass by removing or bypassing the hold is explicitly out of bounds.

## Conditions for removal
All of the following must hold, and each must be demonstrated by a test in the named category:

1. **Verified provenance is required data.** Each entry point accepts only a `FailureContext`
   whose `provenance` was produced by `WorkflowRunProvenanceVerifier` and matches the persisted
   verified webhook event. The check compares installation, event and source repository IDs,
   run ID and attempt, head SHA, and head branch. Missing or mismatched provenance fails closed.
2. **Historical records cannot proceed.** A pipeline without verified provenance is either
   recovered via `recover_historical_provenance` against its linked verified event, or rejected.
3. **Private snapshots.** Checkout and sandbox read repository content from a private snapshot
   that is not shared across incidents, is not writable by untrusted code, and is removed after
   use. Fork-sourced code never runs with installation or write credentials.
4. **Webhook retry enforcement.** `Retry-After` and rate-limit reset values are enforced as
   delays, not only recognised. Provider-directed delays are honoured in full, and a spent
   attempt budget ends in a terminal `exhausted` state. Operator recovery must be authenticated
   and audited. Scheduled retries are executed by a separate worker process; that worker must
   not bypass this hold.
5. **Approver authorisation is configured.** Slack approval is limited to the configured approver
   user, workspace, and channel, with default deny when unconfigured (already implemented; must
   stay covered by tests).
6. **Hold replaced, not deleted.** The unconditional raise is replaced by the data-dependent
   verification from condition 1. This ADR is superseded in the same change.

## Additional removal blockers (Phase 15 review)

The following must be resolved before the hold is lifted. They are blockers, not notes.

1. **Mutation target for fork-sourced runs.** *Resolved in Phase 1.7.* Fork-sourced incidents are refused before any I/O (`require_same_repository` in `mutation_service.py`): the verified event and source repository IDs must be equal, and the target repository must report `fork: false`. Nothing is substituted.
2. **Pull request base branch.** *Resolved in Phase 1.7 (owner decision).* For same-repository runs the base is the exact verified source branch of the failing workflow, and the head is the Akesis remediation branch in the same event repository. A failure on the default branch therefore yields a PR against the default branch; otherwise the default branch is not touched. The remediation commit's only parent is the approved commit, so the PR contains only the approved, validated change. A missing, inaccessible or moved source branch aborts; nothing is rebased and no other branch is substituted.
3. **Sandbox root filesystem.** *Resolved in Phase 1.13.* The validator container runs with `--read-only` and one bounded `/tmp` tmpfs (`rw,exec,nosuid,nodev,size=64m`); the per-run workspace is the only other writable path. Every allowlisted command (`pytest`, `ruff`, `mypy`, `compileall`) was tested on the real `akesis-validator:v1` image under these flags, alongside the existing limits (`--network none`, `--cap-drop ALL`, `no-new-privileges`, uid 1000, `--pids-limit 256`, memory and CPU). See `docs/03-engineering/security.md`, section 4. Open and separate: the deployment guide gives the API service user Docker daemon access, which is root-equivalent on the host. Which process runs validation, and with what Docker access, must be settled before the hold is lifted.
4. **Single database role.** The API and the retry worker use one role. The worker should have a role limited to the tables it needs.
5. **Shared read token.** One token guards every read endpoint and identifies no operator.

## Phase 15 changes that support this ADR

* `require_verified_provenance()` is now the first statement of `process_failure`, `resume_approval` and `create_pull_request`. Before, the provenance read ran first, which contradicted the documented "fail before any database read" invariant.
* `_safe_process_failure`, the only background path that could have reached `process_failure`, was deleted in commit `b40601c`. No route or worker schedules it.
* A census test fails if any `process_failure(` or `resume_approval(` call site appears outside the expected set, or if the webhook route schedules anything except approval resumption.
* A test fails if a remediation or provenance module reads an environment switch, so no test-only setting can alter the hold.

## Required security tests before removal
- **Entry-point gate tests (keep):** every gated method in Decision 2 rejects missing,
  unverified, and mismatched provenance before any side effect.
- **Verifier matrix:** accept valid persisted provenance; reject incorrect repository IDs, SHA,
  branch, run ID, attempt, and installation; reject missing provenance; fail closed on GitHub
  errors.
- **Historical records:** unverified legacy pipelines cannot be approved, resumed, or mutated.
- **Direct SDK guards:** checkout and sandbox calls without trusted provenance perform no I/O.
- **Private snapshot isolation:** fork content cannot reach installation credentials, and
  snapshots are removed after use.
- **PostgreSQL integration:** verified provenance persists and is reloaded exactly, and mismatched
  persisted provenance blocks mutation.
- **Live smoke test (operator):** runbook section 16 onward against a disposable repository.

## Consequences
### Positive
* No remediation path can reach repository content, LLM calls, sandbox execution, Slack cards,
  or PR creation while provenance is unverified.
* Business logic remains fully testable through explicit private bodies and controlled doubles.

### Negative / Limitations
* Remediation is unavailable end-to-end until the removal conditions are met. Webhook ingestion
  and verification still run; the background pipeline stops at the entry gate before it creates
  any pipeline record.
* Test coverage of the full pipeline relies on private bodies, so the public gate path is covered
  by dedicated entry-point tests rather than by every business-logic test.
* Some tests depend on the `_unchecked` naming convention. A future change must keep the gate and
  its body paired.

## Implementation Status (Phase 1.5)

The data-dependent check is implemented and tested against disposable PostgreSQL. The hold remains
active, so no remediation side effect can occur yet.

| Condition | Status |
| :--- | :--- |
| 1. Verified provenance is required data | **Implemented.** `src/packages/shared/persisted_provenance.py` loads the verified webhook event by delivery ID and checks event and source repository IDs and names, installation, run, attempt, SHA, branch, action, status, verified timestamp, freshness (24 hours), semantic key, and the bound pipeline and incident. A provenance object that is not persisted, or that is stored differently, is rejected. Sync SDK entries accept only a `VerifiedProvenance` token issued by this module, bound to their arguments. |
| 2. Historical records cannot proceed | **Implemented for the verifier.** Records without provenance are rejected as `historical`/`missing`. Recovery is handled by `provenance_recovery.py`, which requires exactly one bound, complete, semantically consistent verified event and a fresh installation-scoped verification. It never infers repository IDs or installation context. |
| 3. Private snapshots | **Implemented in ADR-0011** (acquisition, materialization, immutable base, capability boundary). Live verification remains an operator item. |
| 4. `Retry-After` enforcement | **Implemented, subject to the hold.** Provider delays are honoured in full (`webhook_retry.py`). Delays beyond `webhook_retry_provider_delay_ceiling_seconds` become a visible `provider_hold` that is released when due, never shortened. The atomic claim enforces `webhook_retry_max_attempts` and ends in a terminal `exhausted` state with `verification_retries_exhausted`. The retry worker (`python -m src.apps.worker`) claims due deliveries with `FOR UPDATE SKIP LOCKED` and the same atomic transitions, and rebuilds verification input only from persisted identity. Worker-driven admission uses the same code as the webhook route and never invokes remediation. Operators read state through the authenticated, read-only `GET /v1/webhook-deliveries` endpoint. Operator requeue is not implemented. Each verification has a total deadline (240 seconds by default, below the 600-second stale-claim window), so a live claim is not reclaimed while it runs. Two-process behaviour (distribution, no duplicate verification, SIGKILL recovery, SIGTERM) is tested with real worker processes. |
| 5. Approver authorisation | Already implemented and tested. |
| 6. Hold replaced, not deleted | **Not started.** The hold is still unconditional. |

Entry points covered by the matrix (`tests/integration/test_provenance_matrix.py`): process
failure, resume approval, context resolution, fix generation, validation, approval request,
mutation, and sandbox execution (checkout until Phase 1.8 removed it). Each rejects 15 invalid
cases before side effects. A
valid record passes persisted verification and stops at the hold.

Caller-supplied `repo_root` values are rejected at verified entry points, so repository content
can only come from the exact-commit checkout that the token binds.

## Implementation Status (Phase 1.7)

`GitMutationService` no longer clones, uses the shared checkout, or uses the personal access token.
The hold is unchanged and still runs first in `create_pull_request`.

* Source content: a fresh `VerifiedSnapshot` of the approved commit; the validated patch is applied
  verbatim to a workspace copy; links fail closed; the expected tree ID is computed locally.
* Credentials: a GitHub App installation token scoped to the event repository ID with `contents`
  and `pull_requests` write and `metadata` read, held in memory and sent only as a header.
* Remote writes (`src/packages/sdk/github_git_writer.py`): blobs, tree and commit through the Git
  Data API, each verified against local object IDs; the branch is created, never moved or forced.
* Pull request base: the verified source branch (`provenance.head_branch`); head: the remediation
  branch in the same repository.
* Freshness: the source branch must exist, be readable, and point at the approved full SHA. It is
  checked before any remote object, immediately before the branch is created, and immediately
  before the pull request is created.
* Retries reuse one mutation record, an identical existing branch, and an identical open PR. Any
  other existing branch or PR stops the mutation. Akesis never merges.

Tests: `tests/unit/test_mutation_service.py`, `tests/unit/test_github_git_writer.py`,
`tests/unit/test_github_app_auth.py`, `tests/integration/test_mutation_flow.py`.

## Implementation Status (Phase 1.8)

Condition 3 (private snapshots) now covers every path that reads repository content. The hold is
unchanged.

* `CodebaseContextResolver` reads evidence from a verified snapshot workspace of the exact commit and
  removes it afterwards. A rejected or mismatched snapshot yields `unavailable` evidence.
* `GitRepositoryCheckoutManager` (`repo_checkout.py`) and the unused `GitHubClient` are removed, so
  no code clones a repository or embeds a token in `.git/config`.
* The `GITHUB_TOKEN` personal access token setting is removed. Production configuration requires
  `GITHUB_APP_ID` and `GITHUB_APP_PRIVATE_KEY` instead. A leftover `GITHUB_TOKEN` in an environment
  file is ignored.

Tests: `tests/unit/test_context_resolver.py`, `tests/unit/test_provenance_hold.py`,
`tests/unit/test_read_api_security.py`, `tests/integration/test_provenance_matrix.py`.

## Implementation Status (Phase 1.10)

Workflow-log retrieval restores real failure evidence without touching the hold. It is read-only
ingestion, in the same class as provenance verification, and runs in the retry worker:

* Admission persists each verified incident with `log_status = pending`. The worker claims due
  pipelines (`FOR UPDATE SKIP LOCKED`, lease in `log_next_attempt_at`), re-verifies the persisted
  context with `verify_context_provenance` (database only), and only then calls GitHub. A rejected
  check records `unavailable` with a `provenance_*` code and makes no network request.
* Logs are fetched with an installation token scoped to the event repository (`actions:read`,
  `metadata:read`) for the exact verified run attempt. The signed redirect is validated and fetched
  without credentials (see `docs/03-engineering/security.md`, section 5).
* The stored `FailureSignal` is redacted and bounded; provenance is unchanged, so the persisted
  context still verifies. Nothing in this stage calls diagnosis, Gemini, the sandbox or mutation.

Tests: `tests/unit/test_workflow_logs.py`, `tests/integration/test_workflow_log_worker.py`.

## Implementation Status (Phase 1.11)

Diagnosis evidence is bound to persisted, verified state. The hold is unchanged and still raises
first in every gated entry point; nothing schedules diagnosis automatically.

* `load_diagnosis_context` (`src/packages/shared/diagnosis_evidence.py`) returns the persisted
  `FailureContext` for an incident. It checks the incident, commit, repository, run and the full
  provenance object against the caller's context and the pipeline row, then re-verifies provenance
  in PostgreSQL. A caller's stale or forged signal never reaches Gemini.
* `log_status` `pending` or `fetching` raises `LogEvidencePending`, a refusal with no side effects.
  The wait is bounded because the log worker always ends in `extracted` or `unavailable`. With
  `unavailable` logs, diagnosis gets the empty signal and, since Phase 1.12, an explicit
  availability status (see below).
* The worker writes a context only if the stored one is unchanged since it read it
  (`expected_context_json`). Otherwise the write is refused, the lease expires and the attempt is
  retried against the current row.
* `process_failure` calls the loader after `require_verified_provenance()`. While the hold is
  active that call is unreachable, so the loader is tested directly rather than through it.

### Condition 6 handoff (open)

Admission persists each verified incident as a `FAILED` pipeline with the remediation-disabled
reason. `_process_failure_unchecked` returns an existing pipeline for the incident unchanged, so
lifting the hold alone would never diagnose an admitted incident. The admission-to-orchestrator
handoff must be designed together with condition 6.

### Known limitations

L1, L2 and L4 from the Phase 1.11 review are resolved in Phase 1.12. Remaining:

* A disabled log stage (`log_retrieval` unset) leaves incidents `pending`, so diagnosis is refused
  rather than run on missing evidence.

Tests: `tests/integration/test_diagnosis_evidence.py`.

## Implementation Status (Phase 1.12)

Workflow-worker reliability. The hold, approval and mutation architecture are unchanged.

* **Refused writes are reported as refused (L1).** `log_jobs.complete` returns `recorded`,
  `lease_lost` (the row is no longer this attempt's claim: reclaimed, exhausted or finished) or
  `context_changed` (still this attempt's claim, but the stored context changed since it was read).
  The compare-and-swap and `(status, attempt)` guards are unchanged, and a refusal writes nothing.
  The worker counts `logs_extracted`, `logs_retry`, `logs_unavailable` or `logs_rejected` only for
  a recorded write. A refusal counts `logs_lease_lost` or `logs_context_changed` and logs
  `workflow_logs_write_refused` with the reason. A `context_changed` claim is left `fetching` and
  retried after its lease as a new attempt against the current context.
* **Missing logs are stated, not implied (L2).** `load_diagnosis_context` attaches
  `FailureContext.log_evidence` from the pipeline row: `retrieved` for `extracted`, otherwise
  `unavailable` with the stored `log_failure_code` (for example `log_retrieval_exhausted`), reduced
  to `unknown` unless it matches `[a-z0-9_]{1,64}`. The field is excluded from serialization, so it
  never enters `failure_context_json`. The diagnosis prompt states the status before any
  log-derived text. For `unavailable` it gives the reason code, says this is not the same as a run
  with no identifiable failure, and omits the log block, because the empty signal would read as
  logs that held no failure. For `retrieved`, the fenced `untrusted_ci_log` block follows as
  before, and the prompt says that a block naming no error means the logs held no identifiable
  failure. `pending` and `fetching` still raise `LogEvidencePending`.
* **Hard cancellation keeps lease recovery (L4, reviewed).** A cancelled or killed retrieval
  writes nothing and stays `fetching` until its lease (600 seconds by default) expires; the reclaim
  is a new, counted attempt. Immediate release was considered and rejected:
  * SIGTERM never hard-cancels a retrieval. The worker checks its stop flag between claims and
    releases only claims it has not started. A hard cancel comes from SIGKILL, a crash or an
    external task cancellation, and SIGKILL and crashes cannot run release code, so the lease must
    exist anyway.
  * Releasing in the cancelled task would need database I/O during cancellation, shielded from
    the cancel that started it. That can stall shutdown past `TimeoutStopSec` when the database is
    the reason for the cancel.
  * A retrieval may already have reached GitHub, so the attempt must still count, and an early
    release would only bring the next attempt forward.
  The live lease is what prevents a second GitHub retrieval while the first may still be running.
  Diagnosis waits at most one lease per lost attempt, and attempts stay bounded by
  `LOG_RETRIEVAL_MAX_ATTEMPTS`.

Tests (PostgreSQL): cancellation keeps the claim and blocks a second worker until the lease
expires, then recovers as attempt 2; repeated cancellation exhausts the budget; concurrent workers
retrieve a row once; a row locked by another transaction is skipped; a late result after lease
expiry is refused as `lease_lost` and not counted; a concurrent context change is refused as
`context_changed`; unavailable and exhausted logs reach the prompt with their reason code
(`tests/integration/test_workflow_log_worker.py`, `tests/integration/test_diagnosis_evidence.py`,
`tests/unit/test_prompt_builder.py`).

## Implementation Status (Phase 1.14a)

A separate validation executor (`python -m src.apps.executor`) is the only Docker client when
`SANDBOX_EXECUTOR_URL` is set. Unset keeps the local runner, so default behaviour is unchanged. The
protocol and its checks are in `docs/02-architecture/validation-executor.md`.

* **The hold is unchanged and enforced on both sides.** `RemoteSandboxRunner.run_validation` checks
  snapshot binding and then `require_verified_provenance()`, like the local runner. The executor
  also checks the hold on every authenticated job and answers with a signed `rejected/held`, so even
  a caller holding the request key cannot start a container. The real executor process was tested
  to return `held`.
* **Fail closed, no fallback.** In remote mode, every executor failure, rejection, bad signature or
  mismatched echo becomes `INFRASTRUCTURE_ERROR`, and local Docker is never used.
* **No application credentials in the executor.** A stdlib guard runs before `Settings` (and `.env`)
  can load, and refuses to start on `.env`, `DATABASE_URL` or any application credential.
* **Same-host transport:** a Unix socket (0660, owner-only directory), with the SO_PEERCRED caller
  UID allowlist enforced when each connection is accepted. Verification is incomplete: a
  genuinely different OS UID was not tested on this host (no root). The deny path was tested by
  leaving the test's own UID out of the allowlist; a test with a real second user remains to be
  done before production.
* **Not changed in 1.14a:** the approval lifecycle; where `resume_approval` runs (the API); the API's
  Docker group membership (Phase 1.14b); moving remediation execution into the worker (Phase
  1.14c); a separate executor host (Phase 1.14d). The removal blocker in `security.md` about API
  Docker access stays open until 1.14b.

Tests: `tests/unit/test_validation_executor.py`, `tests/integration/test_docker_executor.py`.

## Implementation Status (Phase 1.14b)

Docker daemon access is removed from the API.

* Production `Settings` refuses to load without `SANDBOX_EXECUTOR_URL`.
* In production, `DockerSandboxRunner` refuses construction, and refuses again before any Docker
  subprocess, unless it was built by the executor (`executor=True`). An API or worker process
  therefore cannot run Docker, even through a direct construction.
* Deployment: the API unit no longer requires `docker.service` and joins only `akesis-exec`, to
  reach the 0660 executor socket. Only `akesis-exec` is in the `docker` group, and the runbook no
  longer adds `akesis` to it. Remote failures still fail closed with no fallback.
* Approval resumption still runs in the API (Phase 1.14c is not implemented), so the API remains
  the executor's caller and holds its keys.
* A genuinely different UID was tested without root: a subordinate UID (host 100001, from a user
  namespace) is refused by the 0660 socket's file permissions, and, with the socket temporarily
  opened in the test, by the SO_PEERCRED allowlist. Still not verifiable here without root: a real
  second system user in the socket's group, the systemd units themselves, and
  `SupplementaryGroups`.

## Implementation Status (Phase 1.14c)

Approved remediation executes only in the remediation worker. The hold is unchanged and still
raises first.

* **The API records and queues, nothing more.** The Slack route verifies the signature and the
  approver, workspace and channel, then records the decision. The same conditional update that
  records an approval sets `approvals.execution_status = pending`, so a repeated delivery (a
  duplicate) queues nothing. The API no longer imports the orchestrator or any sandbox runner,
  schedules no background task, and in production refuses to start with any `SANDBOX_EXECUTOR_*`
  value (`SERVICE_ROLE=api`).
* **Durable worker stage** (`approval_jobs`, mirroring `log_jobs`):
  - claims one job at a time, immediately before running it, with `FOR UPDATE SKIP LOCKED` and a
    lease in `execution_next_attempt_at` (a shared batch lease could lapse before a queued job
    started, letting a second worker run the same approval concurrently)
  - every write is guarded by status and attempt, so a lost lease writes nothing
  - bounded attempts (`REMEDIATION_MAX_ATTEMPTS`) with backoff, and lease recovery
  - shutdown claims nothing further, so no attempt is spent on work that never started
  - the claim carries only the approval ID; nothing in a job payload is treated as authorization
* **Each attempt calls the gated `resume_approval`.** The hold comes first. Then persisted
  provenance is re-verified, every approval, incident, repository, commit and proposal binding is
  checked, and, new in this phase, the recorded approver must still be on
  `SLACK_APPROVER_USER_IDS`.
* **Hold behaviour.** `UnverifiedProvenanceError` parks the job as `held`. The approval is kept,
  the attempt is refunded, the job is not claimed again, and no snapshot, Docker or GitHub call
  happens; this is tested with every subprocess and HTTP request patched to fail. Recovery for the
  condition-6 rollout is deliberate: `python -m src.apps.worker.requeue_held`.
* **After the hold, mutation policy is unchanged.** A `FAILED` pipeline is retried, and the mutation
  record makes the retry idempotent. A pipeline left `MUTATING` is terminal
  (`mutation_state_unknown`) and is never re-run automatically. Binding and provenance failures are
  terminal.
* **Identities and credentials.**
  - the API (`akesis`): no Docker, no executor socket, no executor keys
  - the worker (`akesis-worker`): the executor socket group and the executor keys
  - the executor (`akesis-exec`): Docker
  - each service has its own `.env` in its own owner-only working directory, and production
    checks credentials per `SERVICE_ROLE`
  - `GEMINI_API_KEY` and `SLACK_WEBHOOK_URL` have no runtime caller until the condition-6 handoff,
    so neither role requires them; placeholder values are still rejected

Tests: `tests/integration/test_approval_execution.py`, `tests/unit/test_api_docker_isolation.py`,
`tests/unit/test_remediation_entry_points.py` (census: the worker is the only `resume_approval`
call site).
