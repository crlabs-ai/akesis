# ADR-0011: Private Immutable Source Snapshots for Validation

## Status
Accepted (implemented and tested; not yet enabled for live remediation, which remains behind the
temporary hold in ADR-0010).

## Date
2026-11-12

## Context
Validation previously copied a repository tree from the shared, mutable checkout into a sandbox
workspace with `shutil.copytree` and `copy2`. Both follow symbolic links. A repository-controlled
link pointing at a host file therefore placed that host file's contents into the workspace that
Docker mounts. The exposure was reproduced before this decision was implemented.

The mutation path had the same class of problem. Since Phase 1.7 it no longer reads the shared
checkout: it applies the patch to a copy of a verified snapshot (see Consequences).

## Decision

### 1. Acquisition strategy: GitHub Git Data API, not `git clone`
Source is read through `src/packages/sdk/github_git_source.py`. The rejected alternative was a
`git clone` with credentials. A clone would put the installation token in process arguments or
in `.git/config`, and it would run repository-controlled filters on the host. The Git Data API
gives:
- no credential in any argument, file, or Git configuration;
- no repository code executed on the host;
- local verification of every tree and blob object ID, so truncated or substituted responses fail.

The commit object is trusted to the installation-scoped API response over TLS. Trees and blobs are
checked against their Git IDs, and the root tree must equal the commit's tree.

### 2. Identity and SHA binding
- Every request uses the numeric repository ID, never an arbitrary host or URL.
- `/repositories/{id}` must report the expected full name. The commit's URL must belong to that
  repository.
- The commit SHA must equal the verified full SHA from the persisted provenance.
- Tree walking is bounded by depth, request budget, file count, file size, and total size.

### 3. Tree materialization policy
Only `100644` and `100755` regular blobs are materialized. Executable bits are kept only for
`100755`. The following fail closed:
- `120000` symbolic links, including external, nested, dangling, and looping ones. Their targets are
  never requested.
- `160000` submodules (gitlinks) and any other mode or object type.
- Absolute paths, `.` and `..` components, backslashes, NUL bytes, `.git` in any case, and
  case-insensitive path collisions.
- Oversized files, excessive file counts, and oversized totals.
- LFS pointer content, and `.gitattributes` containing `filter=`.

**Git LFS policy:** LFS-tracked content is not supported. Pointer files are refused rather than
validated as though they were the real assets.

### 4. Immutable base and writable workspace
- The base snapshot lives in a private, owner-only directory and is never mounted.
- Each validation copies it into a fresh, separate workspace. Each file is re-hashed as it is copied.
- Only the writable workspace is mounted into Docker. The patch file is kept outside the mount, and
  Git runs with isolated configuration and no repository.
- Read-only permission bits are defence in depth only. Immutability is enforced by a stored digest
  and per-file object-ID verification on every use.

### 5. Capability boundary
- `VerifiedSnapshot` is issued only by `SnapshotService` after successful materialization.
- Every construction, including `dataclasses.replace`, is checked against the issuance registry,
  so an altered copy is a forged object.
- The runner accepts only a live handle. The handle must match its registry record: digest, expiry,
  and release state. The runner also verifies the handle against a persisted provenance token from
  the same incident, pipeline, delivery, repositories, installation, SHA, and branch.
- Provenance tokens use the same registry-backed construction, so an altered token is rejected.

### 6. Lifecycle
Preparation is atomic. The snapshot is removed on success, on preparation error, on timeout, and on
task cancellation. Workspaces are removed after each run, including after Docker failure. Docker
containers are named and force-removed on timeout or cancellation.

## Consequences

### Positive
* The host-symlink exposure is removed from validation. Links are refused before any blob is read.
* Authentication never reaches disk, workspace, or Docker. This is verified by a scan of all files.
* A provenance token alone cannot authorize a sandbox directory.

### Negative / limitations
* Acquisition makes one API request per file. The request budget and file limits bound this, but
  large repositories exceed the default limits and fail closed.
* Commit objects are trusted to the API response, not reconstructed locally.
* The live handle registry is in-process. Handles do not survive a restart and fail closed.
* Phase 1.8: the context resolver reads evidence from a verified snapshot workspace as well. The
  shared checkout (`GitRepositoryCheckoutManager`) and the personal access token setting are
  removed, so no path clones a repository or writes a credential to `.git/config`.
* Phase 1.7: the mutation workflow no longer uses the shared checkout or the personal access token.
  It applies the patch to a workspace copy of a verified snapshot, reads the changes back with links
  refused, and writes blobs, tree, commit, branch and pull request through the Git Data API with an
  installation token scoped to the event repository (ADR-0010, Phase 1.7). Fork-sourced runs never
  reach this point. The pull request targets the verified source branch of the same repository, and
  its single commit sits on the approved snapshot commit, so the snapshot is the only content base.
* Pre-push validation validates base snapshot plus patch, which is exactly the tree that is
  committed: the remote tree must equal the locally computed tree ID.

## Verification
See `tests/unit/test_github_git_source.py`, `tests/unit/test_source_snapshot.py`,
`tests/unit/test_sandbox_runner.py`, `tests/unit/test_mutation_service.py`,
`tests/integration/test_snapshot_boundary.py`, and `tests/integration/test_docker_sandbox.py`.
Live GitHub behaviour is not exercised by these tests and remains an operator smoke-test item.
