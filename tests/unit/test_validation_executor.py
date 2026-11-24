"""Phase 1.14a validation executor: protocol, authentication, integrity and fail-closed client.

No Docker: the executor app runs in-process over httpx.ASGITransport with a recording runner, and
the SO_PEERCRED test runs a real uvicorn server on a Unix socket in tmp.
"""

import asyncio
import json
import os
import socket
import stat
import subprocess
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import uvicorn

from src.apps.executor import app as executor_app
from src.apps.executor.app import (
    EXECUTOR_LABEL,
    ExecutorConfig,
    ExecutorStartupError,
    bind_socket,
    create_app,
    peer_checked_protocol,
    peer_uid,
    require_clean_settings,
    startup_cleanup,
)
from src.apps.executor.guard import UnsafeExecutorEnvironment, refuse_unsafe_environment
from src.packages.sdk.github_git_source import FileEntry
from src.packages.sdk.remote_sandbox_runner import (
    ExecutorRejectedError,
    ExecutorUnavailableError,
    RemoteSandboxRunner,
    default_sandbox_runner,
)
from src.packages.sdk.sandbox_runner import (
    COMMAND_INVOCATION_MAP,
    DockerSandboxRunner,
    DockerUnavailableError,
    ValidationExecutionResult,
)
from src.packages.shared import config as config_module
from src.packages.shared.config import Settings
from src.packages.shared.executor_protocol import (
    RESPONSE_SIGNATURE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    VALIDATIONS_PATH,
    ExecutorResponse,
    JobLimits,
    JobRejected,
    ManifestEntry,
    ValidationJob,
    encode_request,
    sha256_hex,
    sign_request,
    sign_response,
    verify_patch,
)
from src.packages.shared.models import ValidationCommand
from src.packages.shared.source_snapshot import SnapshotService, _blob_sha, _digest, _tree_sha
from tests.support.fake_github import FakeGitHub, FakeInstallationAuth

REQ = "q" * 40
RESP = "r" * 40
COMMIT = "c" * 40
FILES = {
    "pkg/calc.py": b"def add(a, b):\n    return a + b\n",
    "run.sh": b"#!/bin/sh\necho ok\n",
}
PATCH = (
    "--- a/pkg/calc.py\n"
    "+++ b/pkg/calc.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def add(a, b):\n"
    "-    return a + b\n"
    "+    return b + a\n"
)


class RecordingRunner:
    def __init__(self, block: asyncio.Event | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.block = block

    async def _run_in_workspace(
        self, workspace: Path, patch: str, command: list[str], timeout: float
    ) -> ValidationExecutionResult:
        files = {
            str(p.relative_to(workspace)): p.read_bytes()
            for p in workspace.rglob("*")
            if p.is_file()
        }
        writable = all(os.access(p, os.W_OK) for p in workspace.rglob("*"))
        self.calls.append(
            {"workspace": workspace, "files": files, "writable": writable, "command": command}
        )
        if self.block is not None:
            await self.block.wait()
        return ValidationExecutionResult(
            exit_code=0, stdout="ok", stderr="", duration_ms=1.5, timed_out=False
        )


def _config(tmp_path: Path, **overrides: Any) -> ExecutorConfig:
    work = tmp_path / "work"
    work.mkdir(mode=0o700, exist_ok=True)
    values: dict[str, Any] = {
        "socket_path": str(tmp_path / "exec.sock"),
        "allowed_uids": str(os.getuid()),
        "request_key": REQ,
        "response_key": RESP,
        "work_dir": str(work),
    }
    values.update(overrides)
    return ExecutorConfig(**values)


def _job(
    files: dict[str, bytes] = FILES,
    *,
    patch: str = PATCH,
    modes: dict[str, str] | None = None,
    **overrides: Any,
) -> tuple[dict[str, Any], bytes]:
    paths = sorted(files)
    modes = modes or {}
    entries: list[dict[str, Any]] = [
        {
            "path": p,
            "mode": modes.get(p, "100755" if p.endswith(".sh") else "100644"),
            "blob_sha": _blob_sha(files[p]),
            "size": len(files[p]),
        }
        for p in paths
    ]
    try:
        tree = _tree_sha({e["path"]: (e["mode"], e["blob_sha"]) for e in entries})
    except TypeError:  # a file/directory conflict has no tree; the path check runs first anyway
        tree = "0" * 40
    digest = _digest(
        COMMIT, tree, [FileEntry(e["path"], e["mode"], e["blob_sha"]) for e in entries]
    )
    archive = b"".join(files[p] for p in paths)
    now = datetime.now(UTC)
    job: dict[str, Any] = {
        "job_id": uuid.uuid4().hex,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=120)).isoformat(),
        "incident_id": "inc_exec",
        "pipeline_id": "pipe_inc_exec",
        "event_repository_id": 1001,
        "source_repository_id": 1001,
        "commit_sha": COMMIT,
        "root_tree_sha": tree,
        "snapshot_digest": digest,
        "manifest": entries,
        "archive_sha256": sha256_hex(archive),
        "command": "pytest",
        "patch": patch,
        "patch_sha256": sha256_hex(patch.encode()),
    }
    job.update(overrides)
    return job, archive


def _signed(
    job: dict[str, Any], archive: bytes, *, key: str = REQ, ts: int | None = None
) -> tuple[bytes, dict[str, str]]:
    body = encode_request(json.dumps(job).encode(), archive)
    stamp = int(time.time()) if ts is None else ts
    return body, {TIMESTAMP_HEADER: str(stamp), SIGNATURE_HEADER: sign_request(key, body, stamp)}


async def _post(app: Any, body: bytes, headers: dict[str, str]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://executor") as client:
        return await client.post(VALIDATIONS_PATH, content=body, headers=headers)


def _verified(response: httpx.Response, request_signature: str) -> ExecutorResponse:
    expected = sign_response(RESP, request_signature, response.content)
    assert response.headers[RESPONSE_SIGNATURE_HEADER] == expected  # every response is signed
    return ExecutorResponse.model_validate_json(response.content)


@pytest.fixture
def runner() -> RecordingRunner:
    return RecordingRunner()


@pytest.fixture
def app(tmp_path: Path, runner: RecordingRunner) -> Any:
    return create_app(_config(tmp_path), gate=lambda: None, runner=runner)


async def _submit(app: Any, job: dict[str, Any], archive: bytes, **kw: Any) -> ExecutorResponse:
    body, headers = _signed(job, archive, **kw)
    return _verified(await _post(app, body, headers), headers[SIGNATURE_HEADER])


# --- accepted job --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_job_runs_the_allowlisted_command_in_a_verified_writable_workspace(
    app: Any, runner: RecordingRunner, tmp_path: Path
) -> None:
    job, archive = _job()
    result = await _submit(app, job, archive)
    assert (result.status, result.exit_code, result.stdout) == ("passed", 0, "ok")
    assert (result.job_id, result.snapshot_digest, result.patch_sha256) == (
        job["job_id"],
        job["snapshot_digest"],
        job["patch_sha256"],
    )
    (call,) = runner.calls
    assert call["command"] == COMMAND_INVOCATION_MAP[ValidationCommand.PYTEST]
    assert call["files"] == FILES and call["writable"]
    assert not call["workspace"].exists()  # always removed
    assert list((tmp_path / "work").iterdir()) == []


@pytest.mark.asyncio
async def test_hold_is_enforced_by_the_executor_with_a_signed_held_rejection(
    tmp_path: Path, runner: RecordingRunner
) -> None:
    held = create_app(_config(tmp_path), runner=runner)  # production gate
    job, archive = _job()
    result = await _submit(held, job, archive)
    assert (result.status, result.reject_code, result.job_id) == ("rejected", "held", job["job_id"])
    assert runner.calls == []


# --- authentication, expiry, replay --------------------------------------------------------------


@pytest.mark.asyncio
async def test_bad_or_missing_signatures_are_rejected(app: Any, runner: RecordingRunner) -> None:
    job, archive = _job()
    body, headers = _signed(job, archive, key="x" * 40)  # wrong key
    assert _verified(await _post(app, body, headers), headers[SIGNATURE_HEADER]).reject_code == (
        "auth"
    )
    body, headers = _signed(job, archive)
    tampered = body[:-1] + bytes([body[-1] ^ 1])
    response = await _post(app, tampered, headers)
    assert _verified(response, headers[SIGNATURE_HEADER]).reject_code == "auth"
    response = await _post(app, body, {TIMESTAMP_HEADER: headers[TIMESTAMP_HEADER]})
    assert _verified(response, "").reject_code == "auth"
    assert runner.calls == []


@pytest.mark.asyncio
async def test_clock_skew_and_expired_jobs_are_rejected(app: Any, runner: RecordingRunner) -> None:
    job, archive = _job()
    assert (await _submit(app, job, archive, ts=int(time.time()) - 61)).reject_code == "auth"
    now = datetime.now(UTC)
    for issued, expires in (
        (now - timedelta(seconds=30), now - timedelta(seconds=1)),  # expired
        (now, now + timedelta(seconds=301)),  # lifetime over 300 s
        (now, now),  # empty lifetime
        (now + timedelta(seconds=120), now + timedelta(seconds=200)),  # issued in the future
    ):
        job, archive = _job(issued_at=issued.isoformat(), expires_at=expires.isoformat())
        assert (await _submit(app, job, archive)).reject_code == "expired"
    assert runner.calls == []


@pytest.mark.asyncio
async def test_replayed_job_is_rejected(app: Any, runner: RecordingRunner) -> None:
    job, archive = _job()
    assert (await _submit(app, job, archive)).status == "passed"
    assert (await _submit(app, job, archive)).reject_code == "replay"
    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_jobs_issued_before_a_restart_are_rejected(
    tmp_path: Path, runner: RecordingRunner
) -> None:
    job, archive = _job()
    restarted = create_app(
        _config(tmp_path), gate=lambda: None, runner=runner, clock=lambda: time.time() + 5
    )
    assert (await _submit(restarted, job, archive)).reject_code == "stale"
    assert runner.calls == []


# --- schema, allowlist, size ---------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [{"image": "alpine"}, {"docker_args": ["--privileged"]}, {"env": {"A": "1"}}, {"timeout": 9}],
)
async def test_caller_cannot_supply_docker_settings(
    app: Any, runner: RecordingRunner, extra: dict[str, Any]
) -> None:
    job, archive = _job(**extra)
    assert (await _submit(app, job, archive)).reject_code == "schema"
    assert runner.calls == []


@pytest.mark.asyncio
async def test_unknown_manifest_fields_and_invalid_commands_are_rejected(app: Any) -> None:
    job, archive = _job()
    job["manifest"][0]["mount"] = "/:/host"
    assert (await _submit(app, job, archive)).reject_code == "schema"
    for command in ("bash", "pytest -x", "sh -c id", ""):
        job, archive = _job(command=command)
        assert (await _submit(app, job, archive)).reject_code == "schema"


@pytest.mark.asyncio
async def test_invalid_file_modes_are_rejected(app: Any) -> None:
    for mode in ("120000", "160000", "100777", "040000"):
        job, archive = _job(modes={"run.sh": mode})
        assert (await _submit(app, job, archive)).reject_code == "schema"


@pytest.mark.asyncio
async def test_oversized_or_undeclared_bodies_are_rejected(
    tmp_path: Path, runner: RecordingRunner
) -> None:
    small = create_app(_config(tmp_path, max_body_bytes=1024), gate=lambda: None, runner=runner)
    job, archive = _job({"big.py": b"x" * 4096})
    assert (await _submit(small, job, archive)).reject_code == "too_large"

    async def chunks() -> Any:
        yield b"x" * 10  # streamed without a Content-Length

    transport = httpx.ASGITransport(app=small)
    async with httpx.AsyncClient(transport=transport, base_url="http://e") as client:
        response = await client.post(VALIDATIONS_PATH, content=chunks())
    assert ExecutorResponse.model_validate_json(response.content).reject_code == "too_large"
    assert runner.calls == []


@pytest.mark.asyncio
async def test_size_limits_inside_the_job_are_enforced(app: Any) -> None:
    job, archive = _job({"big.py": b"x" * 1_000_001})
    assert (await _submit(app, job, archive)).reject_code == "limits"
    big_patch = "+" * 200_001
    job, archive = _job(patch=big_patch)
    assert (await _submit(app, job, archive)).reject_code == "limits"


# --- snapshot integrity --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_blob_tree_and_digest_mismatches_are_rejected(
    app: Any, runner: RecordingRunner
) -> None:
    job, archive = _job()
    assert (await _submit(app, job, archive + b"!")).reject_code == "integrity"  # size/hash

    swapped = archive[:-1] + b"?"  # same length, archive hash recomputed, blob no longer matches
    job, _ = _job(archive_sha256=sha256_hex(swapped))
    assert (await _submit(app, job, swapped)).reject_code == "integrity"

    job, archive = _job(root_tree_sha="d" * 40)
    assert (await _submit(app, job, archive)).reject_code == "integrity"

    # A forged tree with a digest made consistent with it: only the tree recomputation catches it.
    job, archive = _job()
    entries = [FileEntry(e["path"], e["mode"], e["blob_sha"]) for e in job["manifest"]]
    job.update(root_tree_sha="d" * 40, snapshot_digest=_digest(COMMIT, "d" * 40, entries))
    assert (await _submit(app, job, archive)).reject_code == "integrity"

    job, archive = _job(snapshot_digest="e" * 64)
    assert (await _submit(app, job, archive)).reject_code == "integrity"

    job, archive = _job()
    job["manifest"] = list(reversed(job["manifest"]))  # sizes now slice the archive wrongly
    assert (await _submit(app, job, archive)).reject_code == "integrity"
    assert runner.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "files",
    [
        {"../escape.py": b"x"},
        {"/etc/passwd": b"x"},
        {".git/config": b"x"},
        {"a/.GIT/hooks": b"x"},
        {"a/./b.py": b"x"},
        {"a//b.py": b"x"},
        {"a\\b.py": b"x"},
        {"Readme.md": b"x", "README.md": b"y"},
        {"a": b"x", "a/b.py": b"y"},
    ],
)
async def test_unsafe_workspace_paths_are_rejected(
    app: Any, runner: RecordingRunner, files: dict[str, bytes]
) -> None:
    job, archive = _job(files)
    assert (await _submit(app, job, archive)).reject_code == "path"
    assert runner.calls == []


# --- patch safety --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tampered_patch_is_rejected(app: Any, runner: RecordingRunner) -> None:
    job, archive = _job()
    job["patch"] = PATCH.replace("b + a", "evil()")
    assert (await _submit(app, job, archive)).reject_code == "integrity"
    assert runner.calls == []


@pytest.mark.parametrize(
    "patch, code",
    [
        ("--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-a\n+b\n", "path"),
        ("--- /dev/null\n+++ b/.git/hooks/pre-commit\n@@ -0,0 +1 @@\n+x\n", "path"),
        ("diff --git a/x b/../y\n", "path"),
        ("rename from x\nrename to ../y\n", "path"),
        ("diff --git a/x b/x\nnew file mode 120000\n", "patch"),
        ("diff --git a/x b/x\nGIT binary patch\nliteral 0\n", "patch"),
        ("Binary files a/x and b/x differ\n", "patch"),
        ("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\0\n", "patch"),
        ("--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n a\n?junk\n", "patch"),
    ],
)
def test_unsafe_patches_are_rejected(patch: str, code: str) -> None:
    with pytest.raises(JobRejected) as refused:
        verify_patch(patch, sha256_hex(patch.encode()), JobLimits())
    assert refused.value.code == code


def test_hunk_content_that_looks_like_a_header_is_not_misread() -> None:
    patch = "--- a/q.sql\n+++ b/q.sql\n@@ -1,2 +1,2 @@\n--- ../../etc/passwd\n+++++ ../../x\n \n"
    verify_patch(patch, sha256_hex(patch.encode()), JobLimits())


# --- concurrency ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrency_limit_rejects_extra_jobs_as_busy(tmp_path: Path) -> None:
    release = asyncio.Event()
    runner = RecordingRunner(block=release)
    app = create_app(_config(tmp_path, max_concurrency=1), gate=lambda: None, runner=runner)
    first = asyncio.create_task(_submit(app, *_job()))
    while not runner.calls:
        await asyncio.sleep(0.01)
    assert (await _submit(app, *_job())).reject_code == "busy"
    release.set()
    assert (await first).status == "passed"
    assert len(runner.calls) == 1


# --- client: fail closed, never local Docker -----------------------------------------------------


@pytest.fixture
def snapshot_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "snapshots"
    monkeypatch.setattr(config_module.settings, "snapshot_base_dir", str(root))
    return root


def _token() -> Any:
    return SimpleNamespace(
        incident_id="inc_exec",
        pipeline_id="pipe_inc_exec",
        delivery_id="d-exec",
        event_repository_id=1001,
        source_repository_id=1001,
        owner="crlabs-ai",
        name="akesis",
        installation_id=42,
        head_sha="a" * 40,
        head_branch="main",
    )


def _service() -> SnapshotService:
    fake = FakeGitHub(
        repo_id=1001,
        full_name="crlabs-ai/akesis",
        files={
            path: ("100755" if path.endswith(".sh") else "100644", c) for path, c in FILES.items()
        },
        commit_sha="a" * 40,
    )
    return SnapshotService(auth=FakeInstallationAuth(), client_factory=fake.client)


@pytest.fixture
def no_docker(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Any Docker or other subprocess in the caller fails the test."""

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"subprocess in remote mode: {args!r}")

    async def refuse_async(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"subprocess in remote mode: {args!r}")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse_async)
    yield


def _remote(transport: httpx.AsyncBaseTransport) -> RemoteSandboxRunner:
    return RemoteSandboxRunner(
        url="unix:///nonexistent.sock", request_key=REQ, response_key=RESP, transport=transport
    )


@pytest.mark.asyncio
async def test_remote_runner_returns_the_executor_result(
    snapshot_root: Path, tmp_path: Path, runner: RecordingRunner, no_docker: None
) -> None:
    app = create_app(_config(tmp_path), gate=lambda: None, runner=runner)
    async with _service()._open_unchecked(_token()) as snapshot:
        result = await _remote(httpx.ASGITransport(app=app))._run_validation_unchecked(
            snapshot, PATCH, ValidationCommand.RUFF
        )
    assert result == ValidationExecutionResult(0, "ok", "", 1.5, False)
    assert runner.calls[0]["command"] == COMMAND_INVOCATION_MAP[ValidationCommand.RUFF]
    assert runner.calls[0]["files"] == FILES


@pytest.mark.asyncio
async def test_unreachable_executor_fails_closed_without_local_docker(
    snapshot_root: Path, no_docker: None
) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with _service()._open_unchecked(_token()) as snapshot:
        with pytest.raises(ExecutorUnavailableError):
            await _remote(httpx.MockTransport(down))._run_validation_unchecked(
                snapshot, PATCH, ValidationCommand.PYTEST
            )
    assert issubclass(ExecutorUnavailableError, DockerUnavailableError)  # → INFRASTRUCTURE_ERROR


def _respond(make: Any) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        job = json.loads(request.content[4 : 4 + int.from_bytes(request.content[:4], "big")])
        response: httpx.Response = make(job, request.headers[SIGNATURE_HEADER])
        return response

    return httpx.MockTransport(handler)


def _signed_reply(body: dict[str, Any], key: str, request_sig: str) -> httpx.Response:
    raw = ExecutorResponse(**body).model_dump_json().encode()
    return httpx.Response(
        200, content=raw, headers={RESPONSE_SIGNATURE_HEADER: sign_response(key, request_sig, raw)}
    )


def _passed(job: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body = {
        "job_id": job["job_id"],
        "snapshot_digest": job["snapshot_digest"],
        "patch_sha256": job["patch_sha256"],
        "status": "passed",
        "exit_code": 0,
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_forged_or_substituted_responses_fail_closed(
    snapshot_root: Path, no_docker: None
) -> None:
    captured: list[httpx.Response] = []

    def first(job: dict[str, Any], sig: str) -> httpx.Response:
        response = _signed_reply(_passed(job), RESP, sig)
        captured.append(response)
        return response

    forgeries = {
        "unsigned": lambda job, sig: httpx.Response(
            200, content=ExecutorResponse(**_passed(job)).model_dump_json().encode()
        ),
        "request key": lambda job, sig: _signed_reply(_passed(job), REQ, sig),
        "another request's response": lambda job, sig: captured[0],
        "another job": lambda job, sig: _signed_reply(_passed(job, job_id="f" * 32), RESP, sig),
        "another snapshot": lambda job, sig: _signed_reply(
            _passed(job, snapshot_digest="0" * 64), RESP, sig
        ),
        "another patch": lambda job, sig: _signed_reply(
            _passed(job, patch_sha256="0" * 64), RESP, sig
        ),
        "passed with failure code": lambda job, sig: _signed_reply(
            _passed(job, exit_code=1), RESP, sig
        ),
        "malformed": lambda job, sig: httpx.Response(
            200, content=b"{}", headers={RESPONSE_SIGNATURE_HEADER: sign_response(RESP, sig, b"{}")}
        ),
    }
    async with _service()._open_unchecked(_token()) as snapshot:
        genuine = await _remote(_respond(first))._run_validation_unchecked(
            snapshot, PATCH, ValidationCommand.PYTEST
        )
        assert genuine.exit_code == 0
        for name, forge in forgeries.items():
            with pytest.raises(ExecutorUnavailableError):
                await _remote(_respond(forge))._run_validation_unchecked(
                    snapshot, PATCH, ValidationCommand.PYTEST
                )
            assert name


@pytest.mark.asyncio
async def test_signed_rejection_is_reported_with_its_code(
    snapshot_root: Path, no_docker: None
) -> None:
    def held(job: dict[str, Any], sig: str) -> httpx.Response:
        body = {**_passed(job), "status": "rejected", "reject_code": "held", "exit_code": None}
        return _signed_reply(body, RESP, sig)

    async with _service()._open_unchecked(_token()) as snapshot:
        with pytest.raises(ExecutorRejectedError) as refused:
            await _remote(_respond(held))._run_validation_unchecked(
                snapshot, PATCH, ValidationCommand.PYTEST
            )
    assert refused.value.code == "held"


def test_response_binding_to_the_request_signature() -> None:
    body = b'{"status":"passed"}'
    assert sign_response(RESP, "a", body) != sign_response(RESP, "b", body)
    assert sign_response(RESP, "a", body) != sign_response(REQ, "a", body)


# --- runner selection ----------------------------------------------------------------------------


def test_remote_mode_selects_only_the_remote_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.packages.shared.mutation_service import GitMutationService
    from src.packages.shared.remediation_orchestrator import RemediationOrchestrator
    from src.packages.shared.validation_service import ValidationService

    assert isinstance(default_sandbox_runner(), DockerSandboxRunner)  # unset: local, unchanged
    for name, value in (
        ("sandbox_executor_url", "unix:///run/akesis-exec/exec.sock"),
        ("sandbox_executor_request_key", REQ),
        ("sandbox_executor_response_key", RESP),
    ):
        monkeypatch.setattr(config_module.settings, name, value)
    orchestrator = RemediationOrchestrator()
    runners = [
        ValidationService().sandbox_runner,
        GitMutationService().validation_service.sandbox_runner,
        getattr(orchestrator.validation_service, "sandbox_runner", None),
        orchestrator.mutation_service.validation_service.sandbox_runner,  # type: ignore[attr-defined]
    ]
    assert all(isinstance(r, RemoteSandboxRunner) for r in runners)


def test_settings_require_a_unix_socket_and_two_distinct_long_keys() -> None:
    Settings.model_validate(
        {
            "sandbox_executor_url": "unix:///run/x.sock",
            "sandbox_executor_request_key": REQ,
            "sandbox_executor_response_key": RESP,
        }
    )
    for url, request_key, response_key in (
        ("https://executor.internal", REQ, RESP),
        ("unix://relative.sock", REQ, RESP),
        ("unix:///run/x.sock", REQ, REQ),
        ("unix:///run/x.sock", "short", RESP),
    ):
        with pytest.raises(ValueError):
            Settings.model_validate(
                {
                    "sandbox_executor_url": url,
                    "sandbox_executor_request_key": request_key,
                    "sandbox_executor_response_key": response_key,
                }
            )


# --- startup guard, configuration, socket, SO_PEERCRED -------------------------------------------


def test_guard_refuses_dotenv_database_url_and_credentials(tmp_path: Path) -> None:
    refuse_unsafe_environment(tmp_path, {"PATH": "/usr/bin", "AKESIS_EXECUTOR_WORK_DIR": "/w"})
    (tmp_path / ".env").write_text("")
    with pytest.raises(UnsafeExecutorEnvironment, match=".env"):
        refuse_unsafe_environment(tmp_path, {})
    (tmp_path / ".env").unlink()
    (tmp_path / ".env").symlink_to("/nonexistent")  # a dangling link still counts
    with pytest.raises(UnsafeExecutorEnvironment):
        refuse_unsafe_environment(tmp_path, {})
    (tmp_path / ".env").unlink()
    for name in (
        "DATABASE_URL",
        "github_app_private_key",
        "Slack_Signing_Secret",
        "READ_API_TOKEN",
    ):
        with pytest.raises(UnsafeExecutorEnvironment) as refused:
            refuse_unsafe_environment(tmp_path, {name: "secret-value"})
        assert "secret-value" not in str(refused.value)


def test_loaded_settings_must_hold_no_credentials() -> None:
    require_clean_settings(Settings.model_construct())
    with pytest.raises(ExecutorStartupError, match="github_app_private_key"):
        require_clean_settings(Settings.model_construct(github_app_private_key="k"))
    with pytest.raises(ExecutorStartupError, match="database_url"):
        require_clean_settings(Settings.model_construct(database_url="postgresql://x/y"))


def test_executor_config_rejects_shared_or_short_keys_and_bad_uids(tmp_path: Path) -> None:
    for overrides in (
        {"response_key": REQ},
        {"request_key": "short"},
        {"allowed_uids": ""},
        {"allowed_uids": "root"},
        {"work_dir": "relative"},
    ):
        with pytest.raises(ValueError):
            _config(tmp_path, **overrides)


def test_socket_is_0660_in_an_owner_only_directory(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    sock = bind_socket(run / "exec.sock")
    try:
        assert stat.S_IMODE(os.lstat(run / "exec.sock").st_mode) == 0o660
    finally:
        sock.close()
    os.chmod(run, 0o770)
    with pytest.raises(ExecutorStartupError):
        bind_socket(run / "exec.sock")
    os.chmod(run, 0o700)
    (run / "file").write_text("x")
    with pytest.raises(ExecutorStartupError):
        bind_socket(run / "file")


def test_peer_uid_reads_the_connected_process() -> None:
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with left, right:
        assert peer_uid(left) == os.getuid()


async def _serve(tmp_path: Path, allowed: frozenset[int]) -> tuple[uvicorn.Server, Any, Path]:
    run = tmp_path / "run"
    run.mkdir(mode=0o700, exist_ok=True)
    path = run / "exec.sock"
    sock = bind_socket(path)
    app = create_app(_config(tmp_path), gate=lambda: None, runner=RecordingRunner())
    server = uvicorn.Server(
        uvicorn.Config(
            app, http=peer_checked_protocol(allowed), lifespan="off", log_level="critical"
        )
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(0.01)
    return server, task, path


@pytest.mark.asyncio
async def test_so_peercred_admits_only_allowlisted_uids(tmp_path: Path) -> None:
    server, task, path = await _serve(tmp_path, frozenset({os.getuid()}))
    body, headers = _signed(*_job())  # issued after the executor started
    try:
        transport = httpx.AsyncHTTPTransport(uds=str(path))
        async with httpx.AsyncClient(transport=transport, base_url="http://e") as client:
            response = await client.post(VALIDATIONS_PATH, content=body, headers=headers)
        assert _verified(response, headers[SIGNATURE_HEADER]).status == "passed"
    finally:
        server.should_exit = True
        await task

    server, task, path = await _serve(tmp_path, frozenset({os.getuid() + 4242}))
    body, headers = _signed(*_job())
    try:
        transport = httpx.AsyncHTTPTransport(uds=str(path))
        async with httpx.AsyncClient(transport=transport, base_url="http://e") as client:
            with pytest.raises(httpx.HTTPError):  # closed before any HTTP is read
                await client.post(VALIDATIONS_PATH, content=body, headers=headers)
    finally:
        server.should_exit = True
        await task


def test_startup_cleanup_removes_only_executor_labelled_containers_and_stale_workspaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_docker(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        out = "c1\nc2\n" if args[0] == "ps" else ""
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(executor_app, "_run_docker", fake_docker)
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    stale = work / "old" / "deep"
    stale.mkdir(parents=True)
    (stale / "f").write_text("x")
    os.chmod(stale, 0o500)  # read-only leftovers are still removed
    assert startup_cleanup(work) == 2
    assert calls == [["ps", "-aq", "--filter", f"label={EXECUTOR_LABEL}"], ["rm", "-f", "c1", "c2"]]
    assert list(work.iterdir()) == []
    os.chmod(work, 0o755)
    with pytest.raises(ExecutorStartupError):  # a shared work dir is refused
        startup_cleanup(work)


def test_manifest_entry_and_job_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValueError):
        ManifestEntry(path="a", mode="100644", blob_sha="a" * 40, size=1, extra=1)  # type: ignore[call-arg]
    job, _ = _job()
    ValidationJob.model_validate_json(json.dumps(job))
