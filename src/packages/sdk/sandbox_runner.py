import asyncio
import contextlib
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from src.packages.shared.config import settings
from src.packages.shared.logging import get_logger
from src.packages.shared.models import ValidationCommand
from src.packages.shared.provenance import require_verified_provenance
from src.packages.shared.source_snapshot import (
    SnapshotRejected,
    create_workspace,
    discard_workspace,
    require_snapshot_bound,
)

logger = get_logger("akesis.sandbox_runner")

# The only writable paths are the per-run workspace bind mount and this tmpfs; the root filesystem
# is read-only. Without it, Python's tempfile falls back to the working directory, so pytest's
# tmp_path would land in the workspace on host disk. It is bounded and counts against --memory.
# exec is allowed: the workspace is already writable and executable and pytest runs repository
# code, so noexec here would only fail legitimate tests that run a script they wrote.
# ponytail: fixed 64 MiB; make it a setting if a real test suite needs more.
SANDBOX_TMPFS = "/tmp:rw,exec,nosuid,nodev,size=64m,mode=1777"

# Containers started by the validation executor carry this label; its startup sweep removes only
# these. The local runner does not label its containers.
EXECUTOR_LABEL = "akesis.executor=1"


def _local_docker_forbidden() -> bool:
    """Phase 1.14b: in production only the validation executor may run Docker."""
    return settings.environment.lower() in ("production", "prod")


COMMAND_INVOCATION_MAP: dict[ValidationCommand, list[str]] = {
    ValidationCommand.PYTEST: ["pytest", "-v"],
    ValidationCommand.RUFF: ["ruff", "check", "."],
    ValidationCommand.MYPY: ["mypy", "."],
    ValidationCommand.PYTHON_SYNTAX: ["python3", "-m", "compileall", "."],
}


# An interrupted `docker create` is allowed to finish for at most this long before its client is
# killed (only a hung daemon takes this long).
CREATE_SETTLE_SECONDS = 30.0
_REMOVE_ATTEMPTS = 3


def _remove_container(ref: str) -> bool:
    """Force-remove one container by exact ID or unique name, and confirm Docker no longer has it.

    `docker rm -f` exits 0 for a container that does not exist, so it cannot confirm anything on
    its own; `docker container inspect` reporting "No such" is the confirmation. Bounded and
    best-effort: returns False and logs after the last attempt, and never raises into the caller.
    """
    for attempt in range(_REMOVE_ATTEMPTS):
        try:
            subprocess.run(
                ["docker", "rm", "-f", ref], capture_output=True, timeout=30, check=False
            )
            probe = subprocess.run(
                ["docker", "container", "inspect", "--format", "{{.Id}}", ref],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if probe.returncode != 0 and "no such" in probe.stderr.lower():
                return True
        except (OSError, subprocess.SubprocessError):
            pass
        if attempt + 1 < _REMOVE_ATTEMPTS:
            time.sleep(0.5)
    logger.error("sandbox_container_cleanup_failed", container=ref)
    return False


async def _spawn_and_communicate(argv: list[str], spawned: list[Any]) -> tuple[Any, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    spawned.append(proc)
    out, err = await proc.communicate()
    return proc.returncode, out, err


async def _run_to_completion(cleanup: Any) -> None:
    """Cleanup finishes even if the caller is cancelled again while it runs."""
    task = asyncio.ensure_future(cleanup)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class SandboxError(Exception):
    """Base exception for sandbox validation errors."""

    pass


class DockerUnavailableError(SandboxError):
    """Raised when Docker daemon is unreachable or not running."""

    pass


class SandboxTimeoutError(SandboxError):
    """Raised when validation execution exceeds allowed time limit."""

    pass


class PatchApplicationError(SandboxError):
    """Raised when git apply rejects the unified diff patch."""

    pass


class SandboxWorkspaceError(SandboxError):
    """Raised when temporary workspace setup or materialization fails."""

    pass


def apply_patch_exact(workspace_dir: Path, patch_diff: str) -> None:
    """Apply a unified diff verbatim to a snapshot workspace (no whitespace fixing).

    The patch file lives outside the workspace, and Git runs with isolated configuration and no
    repository, so no repository-controlled configuration, hook or credential is involved.
    """
    patch_file = workspace_dir.parent / f"{workspace_dir.name}.patch"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(workspace_dir.parent),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        fd = os.open(patch_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(patch_diff)
        check_res = subprocess.run(
            ["git", "apply", "--check", str(patch_file)],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            env=env,
        )
        if check_res.returncode != 0:
            raise PatchApplicationError("git apply --check rejected the patch")
        apply_res = subprocess.run(
            ["git", "apply", str(patch_file)],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            env=env,
        )
        if apply_res.returncode != 0:
            raise PatchApplicationError("git apply rejected the patch")
        logger.info("sandbox_patch_applied_successfully")
    finally:
        patch_file.unlink(missing_ok=True)


class ValidationExecutionResult(NamedTuple):
    """Raw captured execution result from sandbox runner."""

    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool


class SandboxRunnerProtocol(Protocol):
    """Interface for isolated sandbox validation execution."""

    async def run_validation(
        self,
        snapshot: object,
        patch_diff: str,
        command: ValidationCommand,
        timeout_seconds: float | None = None,
        *,
        provenance: object | None = None,
    ) -> ValidationExecutionResult:
        """Executes patch validation in an isolated disposable sandbox."""
        ...


class DockerSandboxRunner:
    """Executes validation inside isolated Docker containers with defense-in-depth security."""

    def __init__(
        self,
        image_name: str | None = None,
        timeout_seconds: float | None = None,
        memory_limit: str | None = None,
        cpu_limit: float | None = None,
        max_output_chars: int | None = None,
        *,
        executor: bool = False,
    ) -> None:
        """`executor=True` only inside the validation executor process (labels its containers).

        Outside it, construction fails in production: the API and worker must never run Docker.
        """
        if not executor and _local_docker_forbidden():
            raise SandboxError(
                "Local Docker validation is disabled in production; "
                "validation runs only in the validation executor (SANDBOX_EXECUTOR_URL)"
            )
        self.image_name = image_name or settings.sandbox_image
        self.timeout_seconds = timeout_seconds or settings.sandbox_timeout_seconds
        self.memory_limit = memory_limit or settings.sandbox_memory_limit
        self.cpu_limit = cpu_limit or settings.sandbox_cpu_limit
        self.max_output_chars = max_output_chars or settings.sandbox_max_output_chars
        self.executor = executor

    async def run_validation(
        self,
        snapshot: object,
        patch_diff: str,
        command: ValidationCommand,
        timeout_seconds: float | None = None,
        *,
        provenance: object | None = None,
    ) -> ValidationExecutionResult:
        """Applies a patch to a verified snapshot's writable copy and runs the command in Docker.

        Requires a persisted-provenance token bound to the snapshot, then the temporary hold. A
        caller-supplied directory is not accepted: the only source is a live `VerifiedSnapshot`.
        """
        require_snapshot_bound(provenance, snapshot)
        require_verified_provenance()
        return await self._run_validation_unchecked(snapshot, patch_diff, command, timeout_seconds)

    async def _run_validation_unchecked(
        self,
        snapshot: object,
        patch_diff: str,
        command: ValidationCommand,
        timeout_seconds: float | None = None,
    ) -> ValidationExecutionResult:
        """Sandbox body. Private: callers must use the gated public method."""
        timeout = timeout_seconds or self.timeout_seconds
        cmd_args = COMMAND_INVOCATION_MAP.get(command)
        if not cmd_args:
            raise SandboxError(f"Unsupported validation command: {command}")

        try:
            workspace = create_workspace(snapshot)
        except SnapshotRejected as err:
            raise SandboxWorkspaceError(f"Snapshot rejected: {err.code}") from None
        logger.info("sandbox_workspace_created", image=self.image_name, command=command)
        try:
            return await self._run_in_workspace(workspace, patch_diff, cmd_args, timeout)
        finally:
            discard_workspace(workspace)
            logger.info("sandbox_workspace_destroyed")

    async def _run_in_workspace(
        self, workspace: Path, patch_diff: str, cmd_args: list[str], timeout: float
    ) -> ValidationExecutionResult:
        """Patch, check Docker, run. Shared by the local path and the validation executor."""
        if not self.executor and _local_docker_forbidden():  # checked again before any subprocess
            raise SandboxError("Local Docker validation is disabled in production")
        if patch_diff and patch_diff.strip():
            self._apply_patch_exact(workspace, patch_diff)
        self._verify_docker_available()
        return await self._execute_container(workspace, cmd_args, timeout)

    def _apply_patch_exact(self, workspace_dir: Path, patch_diff: str) -> None:
        """Applies a unified diff verbatim. The patch file lives outside the mounted workspace."""
        apply_patch_exact(workspace_dir, patch_diff)

    def _verify_docker_available(self) -> None:
        """Verifies Docker daemon is running and accessible."""
        try:
            res = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            if res.returncode != 0:
                raise DockerUnavailableError(f"Docker daemon not running: {res.stderr.strip()}")
        except FileNotFoundError as err:
            raise DockerUnavailableError("Docker executable not found on host PATH") from err
        except subprocess.TimeoutExpired as err:
            raise DockerUnavailableError("Docker daemon connection timed out") from err
        except Exception as err:
            if isinstance(err, DockerUnavailableError):
                raise
            raise DockerUnavailableError(f"Docker connection error: {err}") from err

    async def _execute_container(
        self,
        workspace_dir: Path,
        cmd_args: list[str],
        timeout: float,
    ) -> ValidationExecutionResult:
        """Runs an isolated container. Timeout, cancellation or any error force-removes it.

        Create and start are separate so that cleanup always knows whether the container exists.
        The daemon finishes an in-flight create even when its client is killed, so killing a
        `docker run` client mid-create could leave a `Created` container that cleanup had already
        looked for and not found. A create is therefore never cut short: on timeout or
        cancellation it is allowed to finish (bounded), and then the container is removed by its
        unique name. After the create, every action targets the exact container ID it returned.
        """
        if not self.executor and _local_docker_forbidden():
            raise SandboxError("Local Docker validation is disabled in production")
        start_time = time.perf_counter()
        deadline = time.monotonic() + timeout  # covers create and run, as `docker run` did
        container = f"akesis-val-{uuid.uuid4().hex[:16]}"

        docker_cmd = [
            "docker",
            "create",
            "--rm",
            "--name",
            container,
            *(("--label", EXECUTOR_LABEL) if self.executor else ()),
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            SANDBOX_TMPFS,
            "--user",
            "1000:1000",
            "--pids-limit",
            "256",
            "--memory",
            self.memory_limit,
            f"--cpus={self.cpu_limit}",
            "-v",
            f"{workspace_dir.resolve()}:/workspace:rw",
            "-w",
            "/workspace",
            self.image_name,
        ] + cmd_args

        logger.info("validation_container_invoked", cmd=cmd_args, timeout=timeout)
        timed_out = SandboxTimeoutError(f"Sandbox execution timed out after {timeout}s")

        spawned: list[Any] = []
        creating = asyncio.ensure_future(_spawn_and_communicate(docker_cmd, spawned))
        try:
            created, created_out, created_err = await asyncio.wait_for(
                asyncio.shield(creating), timeout=timeout
            )
        except BaseException as error:
            await _run_to_completion(self._settle_create(creating, spawned, container))
            if isinstance(error, TimeoutError):
                raise timed_out from None
            raise
        if created != 0:
            # Like `docker run`, a daemon-side failure (for example a missing image) is exit 125.
            await _run_to_completion(asyncio.to_thread(_remove_container, container))
            return ValidationExecutionResult(
                exit_code=125,
                stdout="",
                stderr=self._truncate_output(created_err.decode("utf-8", errors="replace")),
                duration_ms=round((time.perf_counter() - start_time) * 1000.0, 2),
                timed_out=False,
            )

        ref = created_out.decode("utf-8", errors="replace").strip() or container
        remaining = deadline - time.monotonic()
        if remaining <= 0:  # the create used the whole budget: never start it
            await _run_to_completion(asyncio.to_thread(_remove_container, ref))
            raise timed_out
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "start",
                "--attach",
                ref,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=remaining
            )
        except TimeoutError:
            await _run_to_completion(self._stop_container(proc, ref))
            raise timed_out from None
        except BaseException:
            # Cancellation or any other failure: never leave the container running.
            await _run_to_completion(self._stop_container(proc, ref))
            raise

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        return ValidationExecutionResult(
            exit_code=proc.returncode,
            stdout=self._truncate_output(stdout_bytes.decode("utf-8", errors="replace")),
            stderr=self._truncate_output(stderr_bytes.decode("utf-8", errors="replace")),
            duration_ms=round(duration_ms, 2),
            timed_out=False,
        )

    async def _settle_create(self, creating: Any, spawned: list[Any], container: str) -> None:
        """Let an interrupted create finish (bounded), then remove what it made, by name."""
        try:
            await asyncio.wait_for(creating, timeout=CREATE_SETTLE_SECONDS)
        except Exception:
            # The create client failed or hung. Wait for its process to exit rather than kill
            # it, since a killed client is exactly what leaves a late container behind.
            for proc in spawned:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=CREATE_SETTLE_SECONDS)
                except (TimeoutError, ProcessLookupError):
                    # A hung daemon: the one case this bound cannot settle. The executor's
                    # startup sweep removes labelled leftovers; the name is logged for the rest.
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    logger.error("sandbox_container_create_unsettled", container=container)
        await asyncio.to_thread(_remove_container, container)

    async def _stop_container(self, proc: Any, ref: str) -> None:
        """Kill and reap the client first, so it sends nothing more, then remove the container."""
        if proc is not None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(TimeoutError, ProcessLookupError):
                await asyncio.wait_for(proc.wait(), timeout=10)
        await asyncio.to_thread(_remove_container, ref)

    def _truncate_output(self, text: str) -> str:
        """Truncates voluminous stdout/stderr output defensively."""
        if len(text) <= self.max_output_chars:
            return text
        half = self.max_output_chars // 2
        return text[:half] + "\n\n[... output truncated ...]\n\n" + text[-half:]
