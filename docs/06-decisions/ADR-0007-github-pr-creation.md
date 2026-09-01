# ADR-0007: Safe GitHub Pull Request Creation & Controlled Git Mutation

## Status
Accepted

## Date
2026-09-01

## Context
In Akesis Phase 6, human engineers review and approve fix proposals via Slack, with state durably stored in PostgreSQL. In Phase 7, Akesis executes the first controlled mutation on Git repositories: checking out the target commit, creating a dedicated fix branch, applying the validated diff, running pre-push sandbox validation, pushing the branch, and creating a GitHub Pull Request.

Key safety requirements:
1. **Non-Negotiable Pre-Flight Bounds**: Mutation proceeds ONLY if proposal is valid, validation passed (exit 0), human approval exists and is approved, and all entity commit SHAs match.
2. **Stale Commit Prevention**: HEAD commit in the materialization directory must exactly match `proposal.commit_sha`.
3. **Branch Name Protection**: Branch names must be deterministic (`akesis/fix/<incident>/<proposal>`) and never collide with protected base branches (`main`, `master`, `develop`).
4. **Scope Integrity**: After patch application, the working tree is strictly checked; any unexpected files or modifications abort the pipeline.
5. **Idempotency**: Requests for an already-created PR return existing PR metadata without duplicate branch creation or PR spam.

## Decision
1. **Domain & Database Models**: We add `MutationStatus` (`pending`, `applying`, `validated`, `committed`, `pushed`, `pr_created`, `failed`) and `MutationRecord` domain models, persisted via `MutationModel` in PostgreSQL table `mutations`.
2. **GitHub SDK Protocol**: We extend `GitHubClientProtocol` with `create_pull_request`, `find_pull_request`, and `get_commit`.
3. **Mutation Service**: We implement `GitMutationService` in `src/packages/shared/mutation_service.py` to orchestrate workspace materialization, SHA verification, patch application, pre-push Docker validation, branch push, and PR creation.

## Consequences
### Positive
* Fully deterministic, secure Git mutation workflow.
* Zero unverified or autonomous code pushes.
* Complete traceability from CI failure through approval to GitHub Pull Request.

### Negative / Limitations
* In V1, pull requests require manual merge by repository maintainers; auto-merge and multi-repository sync are deferred to future phases.
