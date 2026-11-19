import asyncio
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

COMMAND_INVOCATION_MAP: dict[ValidationCommand, list[str]] = {
    ValidationCommand.PYTEST: ["pytest", "-v"],
    ValidationCommand.RUFF: ["ruff", "check", "."],
    ValidationCommand.MYPY: ["mypy", "."],
    ValidationCommand.PYTHON_SYNTAX: ["python3", "-m", "compileall", "."],
}


def _force_remove_container(name: str) -> None:
    """Best-effort removal of one named container. Never raises into the caller."""
    try:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        logger.warning("sandbox_container_cleanup_failed", container=name)


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
    ) -> None:
        self.image_name = image_name or settings.sandbox_image
        self.timeout_seconds = timeout_seconds or settings.sandbox_timeout_seconds
        self.memory_limit = memory_limit or settings.sandbox_memory_limit
        self.cpu_limit = cpu_limit or settings.sandbox_cpu_limit
        self.max_output_chars = max_output_chars or settings.sandbox_max_output_chars

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
            if patch_diff and patch_diff.strip():
                self._apply_patch_exact(workspace, patch_diff)
            self._verify_docker_available()
            return await self._execute_container(workspace, cmd_args, timeout)
        finally:
            discard_workspace(workspace)
            logger.info("sandbox_workspace_destroyed")

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
        """Runs an isolated container. Timeout or cancellation force-removes it by name."""
        start_time = time.perf_counter()
        container = f"akesis-val-{uuid.uuid4().hex[:16]}"

        docker_cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            container,
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
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

        proc = await asyncio.create_subprocess_exec(
            *docker_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            await self._stop_container(proc, container)
            raise SandboxTimeoutError(f"Sandbox execution timed out after {timeout}s") from None
        except asyncio.CancelledError:
            await self._stop_container(proc, container)
            raise

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        return ValidationExecutionResult(
            exit_code=proc.returncode,
            stdout=self._truncate_output(stdout_bytes.decode("utf-8", errors="replace")),
            stderr=self._truncate_output(stderr_bytes.decode("utf-8", errors="replace")),
            duration_ms=round(duration_ms, 2),
            timed_out=False,
        )

    async def _stop_container(self, proc: Any, container: str) -> None:
        """Kill the client, remove the named container, and reap the client process."""
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await asyncio.to_thread(_force_remove_container, container)
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except (TimeoutError, ProcessLookupError):
            pass

    def _truncate_output(self, text: str) -> str:
        """Truncates voluminous stdout/stderr output defensively."""
        if len(text) <= self.max_output_chars:
            return text
        half = self.max_output_chars // 2
        return text[:half] + "\n\n[... output truncated ...]\n\n" + text[-half:]
