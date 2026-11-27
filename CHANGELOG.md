# Changelog

All notable changes to the **Akesis** platform are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added (Phase 2: GitHub sign-in, workspaces and tenant isolation, ADR-0012)
* **GitHub sign-in.** The Akesis GitHub App's user authorization with PKCE (Authlib), single-use browser-bound `state`, server-side HttpOnly sessions with expiry, logout and sign-out-everywhere. GitHub user tokens are encrypted at rest and never reach the browser. New settings: `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `AUTH_REDIRECT_URI`, `AUTH_SESSION_TTL_HOURS`, `AUTH_COOKIE_SECURE`, `AUTH_TOKEN_ENCRYPTION_KEY`.
* **Workspaces.** One personal workspace per user, team workspaces with GitHub-login invitations, and OWNER/ADMIN/MAINTAINER/APPROVER/VIEWER roles with no self-escalation. GitHub App installations are linked per workspace after checking the user's access on GitHub (migration `0010_identity_workspaces`).
* **Tenant isolation.** Incidents, pipelines and deliveries are read only through `/v1/workspaces/{workspace_id}/…`, filtered in SQL by the workspace's installations after a fresh membership check. Pipelines carry the verified `installation_id`, backfilled from verified provenance only.
* **Approvals.** Slack remains the only approval channel; the approver must also be an owner-attested APPROVER+ member of the incident's workspace, checked at decision and again at execution. Only owners set Slack mappings, which record who attested them and when (migration `0011_slack_mapping_attestation`). Existing mappings are unattributed and must be set again by an owner.
* **Console.** `/login`, `/auth/callback`, `/app/onboarding`, `/app/approvals` (read-only), `/app/members`, `/app/settings`, a workspace switcher, and a signed-in gate.

### Removed
* **The global read token.** `READ_API_TOKEN`, `X-Akesis-Read-Token`, the console token form and the global `/v1/incidents`, `/v1/pipelines` and `/v1/webhook-deliveries` routes.

### Fixed (sandbox container user portability)
* **The container runs as the workspace owner.** The validation container ran as a fixed `1000:1000` against a workspace the host process creates owner-only (0700), so it could read and write the workspace only when that process was also UID 1000. On any other account, GitHub's runner (UID 1001) included, the container could not even read the code. It now runs as the numeric UID:GID that owns the workspace, read from the filesystem with `lstat`, never from a request or job. Root (UID or GID 0), a workspace this process does not own, and a non-directory are refused before any Docker call. The workspace stays 0700, and every other restriction is unchanged.
* **The executor no longer needs UID 1000.** The deployment guide no longer pins `akesis-exec` to UID 1000, which often collides with the host's first human account.
* **CI runs every Docker test.** CI now builds `akesis-validator:v1`, prints skip reasons, and sets `AKESIS_REQUIRE_DOCKER_TESTS=1`, which fails the run if any Docker test would be skipped for a missing daemon or image. Before, 17 executor and validator-image tests were skipped in CI.

### Fixed (sandbox container-creation cleanup race)
* **No orphaned `Created` containers.** A timeout or cancellation while Docker was still creating a validation container killed the `docker run` client and ran `docker rm -f` straight away. The daemon finishes an in-flight create even after its client dies, so the removal could find nothing and a container was then left in `Created` indefinitely (`--rm` only acts on exit). Reproduced 3/3 at a 0.05 s timeout.
* **Create and start are now separate.** `docker create` (same options: read-only root, no network, `cap-drop ALL`, `no-new-privileges`, non-root user, CPU, memory and PID limits, executor label) is never cut short: on timeout or cancellation it is allowed to finish, bounded at 30 s, and the container is then removed by its unique name. After the create, everything targets the exact container ID it returned. A failed create still reports exit 125, as `docker run` did.
* **Removal is confirmed.** `docker rm -f` exits 0 even for a missing container, so cleanup now confirms with `docker container inspect` ("No such"), retries a bounded number of times, logs `sandbox_container_cleanup_failed` if it cannot confirm, and never raises. Cleanup is shielded, so a second cancellation cannot cut it short. Timeout and cancellation semantics are otherwise unchanged.

### Security (Phase 1.14c: remediation execution moved to the worker)
* **The API only records decisions.** The Slack interaction no longer schedules remediation. Recording an approval queues one durable job on the approval row (migration `0009_approval_execution`), in the same conditional update, so repeated Slack deliveries queue nothing. The API no longer imports the orchestrator or any sandbox runner.
* **Worker stage.** The remediation worker claims approved jobs one at a time, immediately before running each, with `FOR UPDATE SKIP LOCKED`, attempt-guarded leases, bounded retries with backoff and lease recovery. A claimed batch would have shared one lease across sequential remediations, so a later claim could expire before it started and a second worker could run the same approval concurrently; a PostgreSQL test covers this. Each attempt calls the gated `resume_approval`, so the hold comes first, then persisted provenance, every binding, and now a re-check that the recorded approver is still authorized.
* **Hold.** A held job is parked as `held`: the decision is kept, no attempt is consumed, it is not reclaimed, and no snapshot, Docker or GitHub call happens. `python -m src.apps.worker.requeue_held` is the deliberate recovery for the condition-6 rollout. Each run emits a `remediation_requeue` audit event with the kernel-reported OS identity, timestamp, exact approval IDs, count and resulting state. There is no operator authentication yet; the event says so (`identity_verified: false`).
* **No duplicate PRs.** Failed mutations retry through the idempotent mutation record. A pipeline left `mutating` is terminal and never re-run automatically. Mutation policy is unchanged.
* **Identities and credentials.** `SERVICE_ROLE` (`api` or `worker`) selects the production checks. The API refuses any `SANDBOX_EXECUTOR_*` value. The worker requires the executor and refuses the API-only `GITHUB_WEBHOOK_SECRET`, `SLACK_SIGNING_SECRET` and `READ_API_TOKEN`. `GEMINI_API_KEY` and `SLACK_WEBHOOK_URL` are no longer required by either role, because nothing calls them until the condition-6 handoff; placeholders are still rejected. Deployment uses separate `akesis` (API), `akesis-worker` (executor socket group) and `akesis-exec` (docker group) users, each service with its own `.env` and working directory. The worker's stop timeout is now 540 s, above the 480 s remediation deadline.

### Security (Phase 1.14b: no Docker access for the API)
* **Production requires the executor.** Production `Settings` refuses to load without `SANDBOX_EXECUTOR_URL`. In production, `DockerSandboxRunner` refuses construction, and refuses again before any Docker subprocess, unless it was built by the validation executor (`executor=True`). A real API startup subprocess with full synthetic production configuration fails without the executor URL. With it, every runner is remote and no subprocess starts.
* **Deployment.** The API unit no longer requires `docker.service` and joins only `akesis-exec`, to reach the 0660 executor socket. Only `akesis-exec` is in the `docker` group, and the runbook no longer adds `akesis` to it. Tests check the documented units and docs.
* **Different-UID verification.** Without root, a subordinate UID from a user namespace is refused by the 0660 socket and, with the socket opened in the test only, by SO_PEERCRED, against the real executor process. A real second system user in the socket group, and the systemd units at runtime, could not be tested on this host.
* The approval lifecycle is unchanged: approval resumption, and so the executor keys, stay in the API until Phase 1.14c. The hold is unchanged.

### Security (Phase 1.14a: signed remote validation executor)
* **Executor process.** `python -m src.apps.executor` runs validation behind one signed endpoint, and when `SANDBOX_EXECUTOR_URL` is set it is the only Docker client. `RemoteSandboxRunner` implements `SandboxRunnerProtocol` and is injected through `ValidationService`. Leaving the URL unset keeps local validation unchanged.
* **Authentication.** Requests are signed with HMAC-SHA256 under a request key. Responses are signed under a separate response key and bound to the request signature. There are timestamp, expiry (300 s maximum), job-ID replay and process-start checks.
* **Integrity and allowlists.** Strict schemas reject unknown fields, so callers cannot pass an image, arguments, environment, mounts or resources. Only the four allowlisted commands are accepted. The executor verifies the archive SHA-256, every blob SHA, the recomputed root tree SHA, the snapshot digest and the patch SHA-256, and runs independent path, mode and patch-header checks. Payloads are size-limited.
* **Same-host isolation.** The Unix socket is mode 0660 in an owner-only directory, and the SO_PEERCRED caller UID allowlist is checked when each connection is accepted. A stdlib guard refuses startup on `.env`, `DATABASE_URL` or application credentials before `Settings` can load. Startup removes only containers labelled `akesis.executor=1`, and stale workspaces. Concurrency is bounded. The health endpoint reports status only.
* **Fail closed.** The executor enforces the hold itself (a signed `rejected/held`). Remote failures, rejections, bad signatures and mismatched echoes become `INFRASTRUCTURE_ERROR`, with no local fallback. Local-versus-remote `ValidationResult` parity is tested on the real validator image. The approval lifecycle and the API's Docker access are unchanged; those belong to Phases 1.14b and 1.14c.

### Security (Phase 1.13: read-only sandbox root filesystem)
* **Read-only root.** Validation containers run with `--read-only`. The only writable paths are the per-run workspace at `/workspace` and a `/tmp` tmpfs (`rw,exec,nosuid,nodev,size=64m,mode=1777`), private to each container and counted against the memory limit. Without the tmpfs, Python's `tempfile` falls back to the working directory, so pytest temp files would have landed in the workspace on host disk. `/tmp` allows exec because the workspace is already executable; `noexec` would add no barrier and would fail tests that run scripts they wrote.
* **Cleanup on any error.** Any exception after the container starts, not only a timeout or cancellation, now force-removes it. Before, an unexpected client error left the container running until its command ended.
* **Tested on the real image.** All allowlisted commands pass on `akesis-validator:v1` under the new flags. Docker tests cover root writes, permitted writes, tmpfs bounds, network denial, uid, capabilities, no-new-privileges, memory and PID exhaustion, timeout, cancellation, unexpected errors and concurrent isolation. Existing restrictions are unchanged. Resolves ADR-0010 removal blocker 3.

### Fixed (Phase 1.12: workflow worker reliability)
* **Refused writes are no longer reported as recorded (L1).** `log_jobs.complete` returns `recorded`, `lease_lost` or `context_changed`. Outcome counters (`logs_extracted`, `logs_retry`, `logs_unavailable`, `logs_rejected`) and `workflow_logs_recorded` now appear only for a stored result. A refusal counts `logs_lease_lost` or `logs_context_changed` and logs `workflow_logs_write_refused` with the reason. Guards and retry behaviour are unchanged.
* **Gemini is told when logs are unavailable (L2).** Diagnosis receives `log_evidence` (`retrieved`, or `unavailable` with a bounded reason code such as `log_retrieval_exhausted`), read from the pipeline row and never persisted. With unavailable logs the prompt states the reason, says this is not a run without an identifiable failure, and omits the empty log block. No log evidence is invented, and `pending`/`fetching` still block diagnosis.
* **Hard cancellation reviewed (L4).** Lease-expiry recovery is kept: SIGTERM never hard-cancels a retrieval, SIGKILL and crashes cannot release a claim, and the live lease prevents a duplicate GitHub retrieval. PostgreSQL tests now cover cancellation, lease expiry, recovery, attempt exhaustion, concurrent workers and late results. See ADR-0010, Phase 1.12.

### Changed (Phase 1.11: diagnosis uses only final persisted log evidence)
* **Persisted evidence is authoritative.** `load_diagnosis_context` (`diagnosis_evidence.py`) returns the stored `FailureContext` for the incident after checking incident, commit, repository, run and provenance bindings and re-verifying provenance in PostgreSQL. `process_failure` uses it after the hold, so a caller's stale or forged signal never reaches Gemini. `unavailable` logs keep the empty signal.
* **No diagnosis while logs are in flight.** `pending` and `fetching` raise `LogEvidencePending` (no side effect, retry later). The wait is bounded: the log worker always ends in `extracted` or `unavailable`.
* **No lost updates.** The worker writes a context only if the stored one is still the one it read (`expected_context_json`); otherwise the lease expires and the attempt is retried.
* The remediation hold is unchanged. Nothing schedules diagnosis automatically.
* **Known limitations.** L1, L2 and L4 are resolved in Phase 1.12. Admission's `FAILED` pipeline rows block the orchestrator handoff, which belongs to condition 6. See ADR-0010, Phase 1.11.

### Security (redaction hardening, follows the Phase 1.11 review)
* **Quadratic key=value redaction removed.** The `PASSWORD|SECRET|TOKEN|...` key=value regex backtracked quadratically on hostile input (repeated `PASSWORD_`: 0.91s at 20k characters, 3.7s at 40k, 15.1s at 80k; hours at the 2M parse limit). It is replaced by a possessive key regex plus a forward-only scanner with the same matches (checked against the former regex as an oracle); 2M characters now take about 0.2s. The JWT pattern had the same flaw on repeated `eyJ-` (1.8s at 80k) and is now a segment scanner.
* **Cost model.** Token patterns are possessive, so a failed start costs at most its literal plus minimum length. Private keys are a single forward scan: each block is redacted from BEGIN through the END marker of the same key type, and each END is examined once. Matches are equal to or wider than before: a trailing word boundary is no longer required, so a token may be redacted with trailing `-` characters.
* **No split secrets.** Every cut redacts the complete text before it and the result again after it (`redact_tail` / `redact_head`), so the guarantee does not depend on a fixed overlap margin. At storage time the whole decoded log file is redacted before the 2M parse window is selected from it. A quoted value or a key/value gap of any length within the file is seen whole, and the parser still receives at most the 2M parse limit. Every stored field is bounded (paths, functions and code lines 500 characters) and redacted again after its cut. The prompt path redacts again in the same way. An earlier version of this change redacted only the last `limit + 8448` characters, which missed longer quoted values and gaps, and it dropped a token cut mid-way, which could erase an excerpt that was one long token.
* **Cost.** Redaction is linear in the file, which the existing 20MB per-file archive limit bounds (unchanged). The worst hostile 20MB file takes about 7.6s in the worker thread, against a 120s retrieval deadline; the longest single regex call holds the GIL for about 0.4s.
* **Recognition coverage.** Credentials are recognised when glued to other word characters (`f_ghp_…`, `idAKIA…zzz`, `x_eyJ…`, `éDB_SECRET=…`). Distinctive prefixes (`ghp_`, `github_pat_`, `AKIA`/`ASIA`, `AIza`, `xox?-`, JWT) need no boundary. `sk-` and `basic` occur in ordinary words (`task-`, `nonbasic`), so they must not follow an ASCII letter or digit; `_` and Unicode letters still count as a boundary for them. A key now starts where a run of key characters starts rather than at a word boundary.
* **Quoted secret values.** A quoted value ends at its first unescaped closing quote, so `PASSWORD="ab\"cd ef"` no longer leaves `cd ef"` exposed. With no closing quote, the rest of the text is redacted instead of stopping at the first space. Every value is also searched for further assignments (`SECRET=token: hunter2` redacts `hunter2`), with overlapping spans merged. Coverage is a superset of the former key=value regex by construction, checked span by span on fuzzed input, and an exact reference grammar checks the scanner.
* **Private-key blocks.** A terminated block is redacted whole, from BEGIN through the END marker of the same key type (`RSA `, `EC `, `OPENSSH `, `ENCRYPTED `, or none), whatever its length. An earlier version of this change redacted only the first 8 KiB of a body, so a longer key was partly exposed even when terminated. An END of another key type does not close a block.
* **Deliberate over-redaction.** An unclosed quote, or a private key with no END of its type, redacts the rest of the file, failure lines included. An AWS key glued to more capitals is redacted together with them. A backslash before a quote is treated as an escape even where it is literal (shell single quotes), so the value runs on to a later quote. A key inside a quoted value can extend redaction past the outer closing quote. Each failure mode removes text and never exposes it.
* **Limits.** Recognition is a fixed list of formats and key keywords, not universal secret detection. It does not detect other vendors' token formats, generic high-entropy strings, a secret assigned to a key without a listed keyword, `sk-` or `basic` directly after an ASCII letter or digit, or private keys outside the `-----BEGIN … PRIVATE KEY-----` form (PGP `PRIVATE KEY BLOCK`, for example). The cut guarantee covers secrets recognised in the full text.
* **Off the event loop.** Archive reading, parsing and redaction run in `asyncio.to_thread`, so the worker keeps serving its other stages.

### Security (bounded-time log parser)
* **Six quadratic parser patterns replaced.** In `log_parser.py`, the traceback, frame, pytest-failure, pytest line-reference, ruff and mypy regexes backtracked quadratically on hostile logs (repeated traceback headers: 0.45s at 20k characters, 7.2s at 80k; one long path-like run: 13s at 80k). Each is now a linear scanner that returns exactly what the former regex returned: the same leftmost match, groups and backtracking choice. This is verified differentially against the former parser on 120,000 template-generated cases, real fixtures and edge cases. At the 2M parse limit the worst hostile shape now takes about 0.4s.
* **Frame scan follow-up.** On one newline-free line after a traceback header, each frame candidate searched to the end of the text for a newline that never came, which was quadratic at memchr speed (1.3s at the 2M limit, invisible below about 100k characters). The search now runs only when a newline exists ahead, and a match then consumes through it (0.13s at 2M).
* **Oversized line numbers fail closed.** A digit run that `int()` refuses (over 4,300 digits) used to raise `ValueError` out of the parser and fail the whole retrieval. It is now treated as no match.
* The ANSI, `ModuleNotFoundError` and pip patterns were already linear and are unchanged.

### Added (Phase 1.10: secure workflow log retrieval)
* **Worker log stage.** Verified incidents are persisted with `log_status = pending` (migration `0008_pipeline_log_retrieval`). The retry worker claims them, re-verifies persisted provenance in PostgreSQL before any network request, retrieves the exact run attempt's logs, and stores a `FailureSignal` (category, error, file and line, frames, job and step, bounded excerpt). Outcomes are `extracted` or `unavailable` with a stable code; transient failures retry with backoff up to `LOG_RETRIEVAL_MAX_ATTEMPTS`, and crashed claims are recovered after their lease.
* **Least-privilege access.** A GitHub App installation token scoped to the event repository with `actions:read` and `metadata:read`. The run attempt must match the verified identity.
* **Signed-URL safety.** The redirect is not followed automatically. The URL must be HTTPS on port 443, credential-free, on an allowed host (`LOG_DOWNLOAD_HOSTS`) and resolve to public addresses; it is downloaded without an Authorization header, proxies or `.netrc`, with no further redirect, strict timeouts and a streaming size cap, and is never logged.
* **Untrusted archives.** Read in memory under entry, uncompressed size, per-file and compression-ratio limits; links, devices, encrypted entries and unsafe paths are rejected. Secrets are redacted.
* **Prompt hardening.** Log-derived fields go to Gemini only inside a random-id `untrusted_ci_log` block, separate from verified metadata, with code fences neutralized. `FailureSignal` gains optional `job_name` and `step_name`.

### Changed (Phase 1.9: one verified snapshot per incident)
* **Context is resolved once per incident.** The orchestrator resolves evidence before diagnosis and passes the same package to `diagnose_failure` and fix synthesis, halving snapshot acquisitions (and GitHub API requests) for context. `DiagnosticService` rejects supplied evidence that does not match the incident, commit and repository (`ProvenanceRejected`, code `binding`) and still resolves context itself only when none is supplied.

### Changed (Phase 1.8: snapshot-only source access, personal access token removed)
* **Context resolution reads verified snapshots.** `CodebaseContextResolver.resolve_context` is now async and extracts evidence from a verified snapshot workspace of the exact commit, removed afterwards. A rejected or mismatched snapshot yields `unavailable` evidence instead of a checkout error.
* **Removed:** `GitRepositoryCheckoutManager` (`repo_checkout.py`), the unused `GitHubClient` (`github_client.py`), and the `GITHUB_TOKEN` and `REPO_CHECKOUT_BASE_DIR` settings. No code clones a repository or stores a token in `.git/config`. A leftover `GITHUB_TOKEN` in `.env` is ignored.
* **Production configuration requires `GITHUB_APP_ID` and `GITHUB_APP_PRIVATE_KEY`** instead of `GITHUB_TOKEN`, and `READ_API_TOKEN` must differ from the App private key.
* The provenance matrix drops the removed checkout entry point; the other eight keep all 16 cases.

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
