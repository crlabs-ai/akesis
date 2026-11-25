"""Validation executor service (Phase 1.14a): the only Docker client when remote mode is enabled.

One endpoint, `POST /v1/validations`, takes a signed job (see `executor_protocol`) and returns a
signed result. Image, mounts, environment, network, resources and timeouts are executor
configuration only. The remediation hold is checked on every job, so while it is active every
authenticated job gets a signed `rejected/held` response and no container runs.

Same-host transport: a Unix socket created by this process (mode 0660, in an owner-only directory).
Each connection's peer UID is read with SO_PEERCRED before any byte of HTTP is parsed, and
connections from UIDs outside the allowlist are closed.
"""

import asyncio
import os
import socket
import stat
import struct
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from uvicorn.protocols.http.h11_impl import H11Protocol

from src.packages.sdk.github_git_source import FileEntry
from src.packages.sdk.sandbox_runner import (
    COMMAND_INVOCATION_MAP,
    EXECUTOR_LABEL,
    DockerSandboxRunner,
    DockerUnavailableError,
    PatchApplicationError,
    SandboxTimeoutError,
)
from src.packages.shared.config import Settings, settings
from src.packages.shared.executor_protocol import (
    RESPONSE_SIGNATURE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    VALIDATIONS_PATH,
    ExecutorResponse,
    JobLimits,
    JobRejected,
    ValidationJob,
    decode_request,
    sign_response,
    verify_job_contents,
    verify_patch,
    verify_request,
)
from src.packages.shared.logging import get_logger
from src.packages.shared.provenance import require_verified_provenance
from src.packages.shared.source_snapshot import SnapshotRejected, _remove_tree, materialize

logger = get_logger("akesis.executor")

__all__ = ["EXECUTOR_LABEL", "ExecutorConfig", "create_app", "startup_cleanup"]
# Shared settings fields that must keep their defaults in the executor (defence in depth after the
# pre-import guard in guard.py).
_CREDENTIAL_SETTINGS = (
    "database_url",
    "github_app_id",
    "github_app_private_key",
    "github_webhook_secret",
    "gemini_api_key",
    "slack_webhook_url",
    "slack_signing_secret",
    "read_api_token",
    "sandbox_executor_request_key",
    "sandbox_executor_response_key",
)


class ExecutorStartupError(RuntimeError):
    """The executor refuses to start."""


class ExecutorConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AKESIS_EXECUTOR_", env_file=None, extra="ignore")

    socket_path: str
    allowed_uids: str = Field(description="Comma-separated caller UIDs allowed on the socket")
    request_key: str = Field(min_length=32, repr=False)
    response_key: str = Field(min_length=32, repr=False)
    work_dir: str
    max_concurrency: int = Field(default=1, ge=1, le=16)
    max_body_bytes: int = Field(default=24_000_000, ge=1024, le=64_000_000)
    clock_skew_seconds: int = Field(default=60, ge=1, le=300)
    max_job_lifetime_seconds: int = Field(default=300, ge=10, le=900)
    image: str = "akesis-validator:v1"
    timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    memory_limit: str = "512m"
    cpu_limit: float = Field(default=1.0, gt=0, le=16)
    max_output_chars: int = Field(default=4_000, ge=100, le=10_000)

    @field_validator("socket_path", "work_dir")
    @classmethod
    def _absolute(cls, value: str) -> str:
        if not value.startswith("/") or "\0" in value:
            raise ValueError("must be an absolute path")
        return value

    @model_validator(mode="after")
    def _distinct_keys(self) -> "ExecutorConfig":
        if self.request_key == self.response_key:
            raise ValueError("request and response keys must differ")
        self.uids()
        return self

    def uids(self) -> frozenset[int]:
        try:
            uids = frozenset(int(part) for part in self.allowed_uids.split(",") if part.strip())
        except ValueError:
            raise ValueError("allowed_uids must be comma-separated integers") from None
        if not uids or any(uid < 0 for uid in uids):
            raise ValueError("allowed_uids must name at least one UID")
        return uids


def require_clean_settings(loaded: Settings = settings) -> None:
    """After import: the shared settings must hold no credential (nothing came from .env)."""
    dirty = [
        name
        for name in _CREDENTIAL_SETTINGS
        if getattr(loaded, name) != Settings.model_fields[name].default
    ]
    if dirty:
        raise ExecutorStartupError("Executor settings hold credentials: " + ", ".join(dirty))


def _owner_only_dir(path: Path, *, create: bool) -> None:
    if create and not os.path.lexists(path):
        os.mkdir(path, 0o700)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ExecutorStartupError(f"{path} must be a directory owned by the executor")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ExecutorStartupError(f"{path} must not be accessible to group or others")


def _run_docker(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=60, check=False
    )


def startup_cleanup(work_dir: Path) -> int:
    """Remove containers carrying the executor label (and only those) and every stale workspace."""
    _owner_only_dir(work_dir, create=True)
    listed = _run_docker(["ps", "-aq", "--filter", f"label={EXECUTOR_LABEL}"])
    if listed.returncode != 0:
        raise ExecutorStartupError("Docker is unavailable; refusing to start")
    containers = listed.stdout.split()
    if containers:
        _run_docker(["rm", "-f", *containers])
    for child in work_dir.iterdir():
        _remove_tree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
    logger.info("executor_startup_cleanup", containers_removed=len(containers))
    return len(containers)


def bind_socket(path: Path) -> socket.socket:
    """Bind the socket ourselves (uvicorn's own binding would make it 0666)."""
    parent = path.parent
    info = os.lstat(parent)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ExecutorStartupError("Socket directory must be owned by the executor")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise ExecutorStartupError("Socket directory must not be writable by group or others")
    if os.path.lexists(path):
        if not stat.S_ISSOCK(os.lstat(path).st_mode):
            raise ExecutorStartupError("Socket path exists and is not a socket")
        os.unlink(path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    previous = os.umask(0o177)  # created 0600: never briefly wider than the final mode
    try:
        sock.bind(str(path))
    finally:
        os.umask(previous)
    os.chmod(path, 0o660)
    return sock


def peer_uid(sock: socket.socket) -> int:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return int(uid)


def peer_checked_protocol(allowed: frozenset[int]) -> type[H11Protocol]:
    """H11 protocol that closes a connection from a UID outside `allowed` before reading it."""

    class PeerCheckedH11Protocol(H11Protocol):
        def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
            super().connection_made(transport)
            sock = transport.get_extra_info("socket")
            try:
                uid = peer_uid(sock) if sock is not None else -1
            except OSError:
                uid = -1
            if uid not in allowed:
                logger.warning("executor_peer_refused", peer_uid=uid)
                transport.close()

    return PeerCheckedH11Protocol


def _workspace_writable(workspace: Path) -> None:
    """materialize() leaves files read-only; validation tools need to write caches and the patch."""
    for current, dirs, files in os.walk(workspace):
        os.chmod(current, 0o700)
        for name in dirs:
            os.chmod(os.path.join(current, name), 0o700)
        for name in files:
            full = os.path.join(current, name)
            executable = os.stat(full, follow_symlinks=False).st_mode & 0o100
            os.chmod(full, 0o700 if executable else 0o600)


def create_app(
    config: ExecutorConfig,
    *,
    gate: Callable[[], None] = require_verified_provenance,
    runner: Any = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    """`gate` is the remediation hold; it is injected only by tests (code, not an env switch)."""
    runner = runner or DockerSandboxRunner(
        image_name=config.image,
        timeout_seconds=config.timeout_seconds,
        memory_limit=config.memory_limit,
        cpu_limit=config.cpu_limit,
        max_output_chars=config.max_output_chars,
        executor=True,
    )
    work_dir = Path(config.work_dir)
    limits = JobLimits()
    started_at = clock()
    seen: dict[str, float] = {}  # job_id -> expiry; restart safety comes from `started_at`
    slots = asyncio.Semaphore(config.max_concurrency)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz() -> Response:
        """Only "ok" or "unavailable": no versions, paths, image names or Docker details."""
        ok = await asyncio.to_thread(
            lambda: _run_docker(["image", "inspect", config.image]).returncode == 0
        )
        return Response(
            '{"status":"ok"}' if ok else '{"status":"unavailable"}',
            status_code=200 if ok else 503,
            media_type="application/json",
        )

    @app.post(VALIDATIONS_PATH)
    async def validate(request: Request) -> Response:
        signature = request.headers.get(SIGNATURE_HEADER, "")
        job: ValidationJob | None = None

        def reply(status_code: int, **fields: Any) -> Response:
            result = ExecutorResponse(
                job_id=job.job_id if job else "",
                snapshot_digest=job.snapshot_digest if job else "",
                patch_sha256=job.patch_sha256 if job else "",
                **fields,
            )
            body = result.model_dump_json().encode()
            return Response(
                body,
                status_code=status_code,
                media_type="application/json",
                headers={
                    RESPONSE_SIGNATURE_HEADER: sign_response(config.response_key, signature, body)
                },
            )

        def reject(code: str, status_code: int = 400) -> Response:
            logger.warning("executor_job_rejected", code=code, job_id=job.job_id if job else None)
            return reply(status_code, status="rejected", reject_code=code)

        declared = request.headers.get("content-length", "")
        if not declared.isdigit() or int(declared) > config.max_body_bytes:
            return reject("too_large", 413)
        received = bytearray()
        async for chunk in request.stream():
            received += chunk
            if len(received) > config.max_body_bytes:
                return reject("too_large", 413)
        body = bytes(received)

        now = clock()
        timestamp = request.headers.get(TIMESTAMP_HEADER, "")
        if not verify_request(
            config.request_key, body, timestamp, signature, now=now, skew=config.clock_skew_seconds
        ):
            return reject("auth", 401)
        try:
            job_json, archive = decode_request(body)
            job = ValidationJob.model_validate_json(job_json)
        except ValueError:  # includes pydantic ValidationError
            return reject("schema")
        issued, expires = job.issued_at.timestamp(), job.expires_at.timestamp()
        if (
            expires <= now
            or expires <= issued
            or expires - issued > config.max_job_lifetime_seconds
            or issued > now + config.clock_skew_seconds
        ):
            return reject("expired")
        if issued < started_at:
            return reject("stale")  # issued before this process started: never replayable
        for old in [job_id for job_id, expiry in seen.items() if expiry <= now]:
            del seen[old]
        if job.job_id in seen:
            return reject("replay")
        seen[job.job_id] = expires
        try:
            gate()
        except Exception:
            return reject("held", 403)
        try:
            contents = verify_job_contents(job, archive, limits)
            verify_patch(job.patch, job.patch_sha256, limits)
        except JobRejected as error:
            return reject(error.code, 422)
        if slots.locked():
            return reject("busy", 503)
        async with slots:
            fields = await _run(job, contents)
        if fields is None:
            return reject("integrity", 422)
        return reply(200, **fields)

    async def _run(job: ValidationJob, contents: dict[str, bytes]) -> dict[str, Any] | None:
        workspace = work_dir / uuid.uuid4().hex
        os.mkdir(workspace, 0o700)
        try:
            entries = [FileEntry(e.path, e.mode, e.blob_sha) for e in job.manifest]
            try:
                materialize(workspace, entries, contents)  # verifies object IDs and paths again
            except SnapshotRejected:
                return None
            _workspace_writable(workspace)
            try:
                command = COMMAND_INVOCATION_MAP[job.command]
                result = await runner._run_in_workspace(
                    workspace, job.patch, command, config.timeout_seconds
                )
            except PatchApplicationError as error:
                return {"status": "patch_rejected", "stderr": str(error)}
            except SandboxTimeoutError as error:
                return {"status": "timed_out", "stderr": str(error), "timed_out": True}
            except DockerUnavailableError as error:
                return {"status": "infrastructure_error", "stderr": str(error)}
            return {
                "status": "passed" if result.exit_code == 0 else "failed",
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_ms": result.duration_ms,
            }
        finally:
            _remove_tree(workspace)

    return app
