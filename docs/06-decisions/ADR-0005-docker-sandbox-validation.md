# ADR-0005: Docker Sandbox Validation Engine & Command Isolation

## Status
Accepted

## Date
2026-08-30

## Context
In Akesis Phase 4, the Fix Proposal Engine synthesizes structured unified diff proposals. Before code proposals can be presented to human engineers for review (Phase 6) or pull request generation (Phase 7), Akesis must empirically determine whether the proposed change actually fixes the CI failure.

Key architectural and security constraints:
1. Model proposals and repository contents are untrusted data. The LLM must not control shell commands, Docker parameters, or mounts.
2. The real repository on the host machine must remain untouched (zero mutation).
3. The proposed patch must be applied exactly as generated (`git apply --check` followed by `git apply`) without `--whitespace=fix` or automatic rewriting.
4. Validation must occur inside an isolated container with defense-in-depth security (`--network none`, `--cap-drop ALL`, non-root, timeout bounds).
5. Validation commands must come from a deterministic allowlist (`pytest`, `ruff`, `mypy`, `python -m compileall`).

## Decision
1. **Protocol Decoupling**: We define `SandboxRunnerProtocol` in `src/packages/sdk/sandbox_runner.py` and implement `DockerSandboxRunner`. `ValidationService` depends exclusively on the protocol, enabling offline unit testing without requiring Docker.
2. **Ephemeral Workspace Lifecycle**: Disposable temporary directories are created per validation run, materialized at the exact `commit_sha` via `GitRepositoryCheckoutManager`, patched via verbatim `git apply`, and unconditionally destroyed in `finally:` blocks.
3. **Dedicated Validator Image**: `docker/Dockerfile.validator` defines `akesis-validator:v1` based on `python:3.12-slim` containing pre-installed `pytest`, `ruff`, `mypy`, and `git`. No packages are installed dynamically at runtime.
4. **Deterministic Command Allowlist**: Commands are strictly mapped from `FailureContext.signal.category` without allowing arbitrary shell string execution.
5. **In-Memory ValidationResult**: `ValidationResult` is returned directly to the pipeline in-memory. PostgreSQL table migrations are deferred to Phase 6/7.

## Consequences
### Positive
* Empirical certainty: Validates whether patches compile, lint, and pass tests before human review.
* Secure isolation: Defense-in-depth isolation ensures network is disabled, capabilities dropped, and real repositories untouched.
* Exactness: Ensures what the model generated is validated verbatim without masking whitespace or hunk bugs.

### Negative / Limitations
* In V1, custom third-party system C libraries outside the pre-installed validator image cannot be dynamically installed during sandbox execution due to `--network none`.
