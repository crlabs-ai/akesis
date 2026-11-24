# Validation Executor (Phase 1.14a)

The validation executor (`python -m src.apps.executor`) is a separate process. When remote mode is
enabled it is the only component that constructs `DockerSandboxRunner` and runs the `docker` CLI.
Callers use `RemoteSandboxRunner`, which implements the existing `SandboxRunnerProtocol` and is
injected through `ValidationService(sandbox_runner=...)`. `GitMutationService` and
`RemediationOrchestrator` pick it up through the same default.

| `SANDBOX_EXECUTOR_URL` | Behaviour |
| :--- | :--- |
| unset (default) | Unchanged: the local `DockerSandboxRunner` runs in the calling process. |
| `unix:///abs/path.sock` | Remote only. No Docker subprocess runs in the caller, and there is no fallback to local Docker. |

Both runners stay behind the remediation hold. The caller runs `require_snapshot_bound` and then
`require_verified_provenance()`, as before. The executor also checks the hold on every
authenticated job. While the hold is active it returns a signed `rejected/held`, and no container
runs.

## Protocol

`POST /v1/validations` over the Unix socket. The body is:

```
4-byte big-endian length of the job JSON || job JSON || archive
```

The archive is every file's bytes concatenated in manifest order, and each manifest entry carries
its size. There is no archive format, so there is nothing to parse.

**Job** (`ValidationJob`, strict, unknown fields rejected):

| Field | Constraint |
| :--- | :--- |
| `job_id` | 32 hex characters (uuid4) |
| `issued_at`, `expires_at` | lifetime at most 300 s |
| `incident_id`, `pipeline_id`, `event_repository_id`, `source_repository_id` | identifiers |
| `commit_sha`, `root_tree_sha` | 40 hex characters |
| `snapshot_digest`, `archive_sha256`, `patch_sha256` | 64 hex characters |
| `manifest[]` | `{path, mode (100644 or 100755), blob_sha, size}`, unknown fields rejected |
| `command` | `pytest`, `ruff`, `mypy` or `python_syntax` only |
| `patch` | the unified diff |

The job has no field for an image, arguments, environment, mounts, network, resources or timeout.
Those are executor configuration (`AKESIS_EXECUTOR_*`) only. A job that adds any such field is
rejected as `schema`.

**Signatures** (HMAC-SHA256, two separate keys, both at least 32 characters, which must differ):

```
X-Akesis-Signature          = HMAC(request_key,  "akesis-exec-req-v1\nPOST\n/v1/validations\n{ts}\n{sha256(body)}")
X-Akesis-Response-Signature = HMAC(response_key, "akesis-exec-resp-v1\n{request signature}\n{sha256(response body)}")
```

The response MAC binds every response to the exact request it answers. A response captured from
one job cannot be replayed for another. A holder of the request key cannot forge results.

**Executor checks, in order.** Every rejection is a signed `{"status": "rejected", "reject_code": ...}`.

1. The peer UID, read with SO_PEERCRED when the connection is accepted, must be in
   `AKESIS_EXECUTOR_ALLOWED_UIDS`. Otherwise the connection is closed before any HTTP is read.
2. The Content-Length must be declared and at most the body limit, and the streamed body is capped
   too (`too_large`).
3. The request signature must be valid and the timestamp within 60 s (`auth`).
4. Framing and schema (`schema`).
5. `expires_at` must be in the future, after `issued_at` and at most 300 s later, and `issued_at`
   must not be in the future (`expired`).
6. `issued_at` must be after the executor process started (`stale`). A job from before a restart
   can never run.
7. The `job_id` must not have been seen before. Seen IDs are kept in memory until they expire
   (`replay`).
8. The remediation hold (`held`).
9. Integrity (`integrity`, `limits`, `path`), independent of the caller:
   - file count, per-file and total size limits
   - the archive SHA-256, and every file's Git blob SHA
   - the root tree SHA, recomputed
   - the snapshot digest
   - the path rules: no empty, `.`, `..` or `.git` component, no `\`, NUL or leading `/`, no
     duplicate or case-colliding paths, and no path that is both a file and a directory
10. Patch checks (`integrity`, `limits`, `patch`, `path`):
    - patch SHA-256, and at most 200 KB
    - every path in the `---`, `+++`, `diff --git`, rename and copy headers (hunk content is not
      misread as headers)
    - no binary patches, NUL bytes or file modes other than 644/755
11. Concurrency: at most `AKESIS_EXECUTOR_MAX_CONCURRENCY` jobs at once, with no queue (`busy`).
12. The files are materialized with `materialize()`, which checks object IDs and paths again, into
    a fresh workspace in the owner-only work directory. Then the existing hardened path runs:
    exact `git apply`, then `docker run` with every restriction unchanged plus the label
    `akesis.executor=1`. The workspace is always removed.

**Response** (`ExecutorResponse`): `job_id`, `snapshot_digest` and `patch_sha256` (echoed), `status`
(`passed`, `failed`, `timed_out`, `patch_rejected`, `infrastructure_error` or `rejected`),
`reject_code`, `exit_code`, and `stdout`/`stderr` (bounded by the runner and by the schema),
`duration_ms` and `timed_out`.

**Caller mapping.** `patch_rejected` raises `PatchApplicationError`, `timed_out` raises
`SandboxTimeoutError`, and `infrastructure_error` raises `DockerUnavailableError`, all with the
executor's message. `ValidationService` therefore produces the same `ValidationResult` as the local
runner; this is tested against the real validator image for every command, a rejected patch, a
missing image and a timeout.

Every other outcome raises a subclass of `DockerUnavailableError`, which becomes
`INFRASTRUCTURE_ERROR` (fail closed):
- the executor is unreachable
- the response is unsigned, wrongly signed or malformed
- the echoed `job_id`, digest or patch hash doesn't match
- `passed` is reported with a non-zero exit code
- any signed `rejected`

`GET /healthz` returns only `{"status":"ok"}` (200) or `{"status":"unavailable"}` (503).

## Startup

1. `guard.py` runs before any Akesis module is imported. It is standard library only, because the
   shared `Settings` loads `.env` from the working directory at import time. The executor refuses
   to start if the working directory contains `.env` (a dangling link counts), or if any of these is
   set in its environment: `DATABASE_URL`, `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`,
   `GITHUB_WEBHOOK_SECRET`, `GEMINI_API_KEY`, `SLACK_WEBHOOK_URL`, `SLACK_SIGNING_SECRET`,
   `READ_API_TOKEN`, `SANDBOX_EXECUTOR_*_KEY`. Names are matched case-insensitively, and values are
   never printed.
2. After import, the shared settings must still hold every credential field at its default.
3. `ExecutorConfig` is read from `AKESIS_EXECUTOR_*` only (no env file).
4. Startup cleanup:
   - `docker rm -f` every container labelled `akesis.executor=1`, and no others; the local runner
     does not label its containers
   - empty the work directory, which must be owned by the executor with no group or other access
5. The executor binds the socket itself, because uvicorn would make it 0666. The socket is created
   with umask 0177 and then set to 0660, in a directory owned by the executor that group and others
   cannot write. A stale socket is replaced; any other file at that path is refused.

## Configuration

Executor (`AKESIS_EXECUTOR_*`):
- **Required:** `SOCKET_PATH`, `ALLOWED_UIDS` (comma-separated), `REQUEST_KEY`, `RESPONSE_KEY`,
  `WORK_DIR`.
- **Optional:** `MAX_CONCURRENCY` (1), `MAX_BODY_BYTES` (24 MB), `CLOCK_SKEW_SECONDS` (60),
  `MAX_JOB_LIFETIME_SECONDS` (300), `IMAGE` (`akesis-validator:v1`), `TIMEOUT_SECONDS` (30),
  `MEMORY_LIMIT` (512m), `CPU_LIMIT` (1.0), `MAX_OUTPUT_CHARS` (4000).

Caller (shared settings): `SANDBOX_EXECUTOR_URL`, `SANDBOX_EXECUTOR_REQUEST_KEY`,
`SANDBOX_EXECUTOR_RESPONSE_KEY`, `SANDBOX_EXECUTOR_TIMEOUT_SECONDS` (120). When the URL is set it
must be `unix:///...`, and the keys must be at least 32 characters and different.

## Transition risks (open after 1.14a)

- **The API keeps Docker access while the URL is unset.** That is today's default. Removing the
  API's `docker` group membership is Phase 1.14b.
- **In remote mode, the API process holds both executor keys**, because approval resumption still
  runs in the API. A compromised API can submit allowlisted validation jobs, which still refuse
  while the hold is active, and can read results. It cannot choose an image, arguments, mounts or
  resources. Moving remediation execution into the worker (Phase 1.14c) removes the keys from the
  API.
- **The executor trusts the caller's provenance attestation.** It verifies content integrity, but
  it has no database or GitHub access with which to prove that this commit belongs to this
  incident.
- **The executor must run as UID 1000** (or share it with the container): the container runs as
  `1000:1000` against an owner-only (0700) workspace. This is unchanged from the local runner.
- **Replay protection is in memory.** It is safe across restarts because of the process-start rule.
  A job signed just before a restart is refused (`stale`) and the caller records
  `INFRASTRUCTURE_ERROR`.
- **No queue:** a busy executor fails closed (`INFRASTRUCTURE_ERROR`).
- **Same host:** a container escape still reaches a host that holds the application secrets, until
  the executor runs on its own credential-free host (Phase 1.14d). HTTPS transport is not
  implemented; only `unix://` is accepted.
- **SO_PEERCRED: verification incomplete.** A caller running as a genuinely different OS user was
  not tested; this host gives no root access with which to create one. The tests prove the
  mechanism with the real kernel credential: a real uvicorn server on a Unix socket serves the
  test's own UID when it is allowlisted, and closes the connection when the allowlist leaves it out.
  Before production, the deny path must also be verified with a real second UID (for example
  `sudo -u nobody curl --unix-socket ...` against the executor), along with group access to the
  0660 socket under the systemd unit.
- **No client-side peer check:** the client does not check the executor socket's peer UID.
  Authenticity rests on the response key, so a process that squats on the socket path cannot
  produce a valid result without that key.
