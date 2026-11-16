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
   - `ValidationService.validate_fix` (checkout and sandbox)
   - `CodebaseContextResolver.resolve_context` (checkout)
   - `ApprovalService.request_approval` (persistence and Slack card)
   - `GitMutationService.create_pull_request` (branch, push, PR)
   - `GitRepositoryCheckoutManager.checkout_commit` (direct SDK clone or fetch)
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

1. **Mutation target for fork-sourced runs.** The checkout, push and pull request use the verified *source* repository (`FailureContext.repository_owner/name`). For a fork-sourced run this targets the fork with installation credentials. ADR-0011 states that fork-sourced code never runs with write credentials. Either refuse fork-sourced incidents, or send every mutation to the event (base) repository.
2. **Pull request base branch.** `base_branch` is the failing run's head branch, which can be a fork branch or a non-default branch. It should be the event repository's default branch.
3. **Sandbox root filesystem.** The validator container runs with a writable root filesystem. It has no PID limit before Phase 15 (now 256), and it has no read-only root or tmpfs. Adding `--read-only` needs a validator-image test first.
4. **Single database role.** The API and the retry worker use one role. The worker should have a role limited to the tables it needs.
5. **Shared read token.** One token guards every read endpoint and identifies no operator.

## Phase 15 changes that support this ADR

* `require_verified_provenance()` is now the first statement of `process_failure`, `resume_approval` and `create_pull_request`. Before, the provenance read ran first, which contradicted the documented "fail before any database read" invariant.
* `_safe_process_failure`, the only background path that could have reached `process_failure`, was deleted. The committed `HEAD` still schedules it, so **`HEAD` must not be deployed**.
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
mutation, checkout, and sandbox execution. Each rejects 15 invalid cases before side effects. A
valid record passes persisted verification and stops at the hold.

Caller-supplied `repo_root` values are rejected at verified entry points, so repository content
can only come from the exact-commit checkout that the token binds.
