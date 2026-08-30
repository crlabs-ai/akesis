import asyncio
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import NamedTuple, Protocol

from src.packages.shared.config import settings
from src.packages.shared.logging import get_logger
from src.packages.shared.models import ValidationCommand

logger = get_logger("akesis.sandbox_runner")

COMMAND_INVOCATION_MAP: dict[ValidationCommand, list[str]] = {
    ValidationCommand.PYTEST: ["pytest", "-v"],
    ValidationCommand.RUFF: ["ruff", "check", "."],
    ValidationCommand.MYPY: ["mypy", "."],
    ValidationCommand.PYTHON_SYNTAX: ["python3", "-m", "compileall", "."],
}


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
        repo_source_dir: Path,
        patch_diff: str,
        command: ValidationCommand,
        timeout_seconds: float | None = None,
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
        repo_source_dir: Path,
        patch_diff: str,
        command: ValidationCommand,
        timeout_seconds: float | None = None,
    ) -> ValidationExecutionResult:
        """Applies patch in disposable workspace and runs command in secure container."""
        timeout = timeout_seconds or self.timeout_seconds
        cmd_args = COMMAND_INVOCATION_MAP.get(command)
        if not cmd_args:
            raise SandboxError(f"Unsupported validation command: {command}")

        temp_dir = tempfile.mkdtemp(prefix="akesis_val_")
        workspace_dir = Path(temp_dir)

        logger.info(
            "sandbox_workspace_created",
            workspace=str(workspace_dir),
            image=self.image_name,
            command=command,
        )

        try:
            # 1. Materialize exact repo files into ephemeral workspace
            self._materialize_workspace(repo_source_dir, workspace_dir)

            # 2. Apply patch exactly as generated (strictly NO --whitespace=fix)
            if patch_diff and patch_diff.strip():
                self._apply_patch_exact(workspace_dir, patch_diff)

            # 3. Check Docker daemon availability
            self._verify_docker_available()

            # 4. Invocate isolated Docker container
            result = await self._execute_container(workspace_dir, cmd_args, timeout)
            return result

        finally:
            # 5. Clean up ephemeral host workspace unconditionally
            try:
                shutil.rmtree(workspace_dir, ignore_errors=True)
                logger.info("sandbox_workspace_destroyed", workspace=str(workspace_dir))
            except Exception as err:
                logger.warning("sandbox_cleanup_error", error=str(err))

    def _materialize_workspace(self, source_dir: Path, target_dir: Path) -> None:
        """Copies source files from checked-out exact commit into disposable workspace."""
        if not source_dir.exists() or not source_dir.is_dir():
            raise SandboxWorkspaceError(f"Source repository directory not found: {source_dir}")

        try:
            # Copy all files ignoring .git directory
            for item in os.listdir(source_dir):
                if item == ".git":
                    continue
                s = source_dir / item
                d = target_dir / item
                if s.is_dir():
                    shutil.copytree(s, d, symlinks=False)
                else:
                    shutil.copy2(s, d)
            # Initialize minimal clean git repo inside workspace to support git apply
            subprocess.run(
                ["git", "init", "-q"],
                cwd=target_dir,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Akesis"],
                cwd=target_dir,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "agent@crlabs.ai"],
                cwd=target_dir,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "add", "."],
                cwd=target_dir,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-q", "-m", "init"],
                cwd=target_dir,
                check=True,
                capture_output=True,
            )
        except Exception as err:
            raise SandboxWorkspaceError(f"Failed to materialize workspace: {err}") from err

    def _apply_patch_exact(self, workspace_dir: Path, patch_diff: str) -> None:
        """Applies unified diff using git apply --check followed by git apply."""
        patch_file = workspace_dir / "patch.diff"
        try:
            patch_file.write_text(patch_diff, encoding="utf-8")

            # First: git apply --check (verifies without modifying)
            check_res = subprocess.run(
                ["git", "apply", "--check", str(patch_file)],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
            )
            if check_res.returncode != 0:
                raise PatchApplicationError(
                    f"git apply --check rejected patch: {check_res.stderr.strip()}"
                )

            # Second: git apply (applies verbatim, no --whitespace=fix)
            apply_res = subprocess.run(
                ["git", "apply", str(patch_file)],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
            )
            if apply_res.returncode != 0:
                raise PatchApplicationError(f"git apply rejected patch: {apply_res.stderr.strip()}")

            logger.info("sandbox_patch_applied_successfully")
        finally:
            if patch_file.exists():
                patch_file.unlink(missing_ok=True)

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
        """Runs isolated Docker container with strict security bounds."""
        start_time = time.perf_counter()

        docker_cmd = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "1000:1000",
            "--memory",
            self.memory_limit,
            f"--cpus={self.cpu_limit}",
            "-v",
            f"{workspace_dir.resolve()}:/workspace:rw",
            "-w",
            "/workspace",
            self.image_name,
        ] + cmd_args

        logger.info(
            "validation_container_invoked",
            cmd=cmd_args,
            timeout=timeout,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
                exit_code = proc.returncode
                timed_out = False
            except TimeoutError:
                timed_out = True
                exit_code = None
                try:
                    proc.kill()
                except Exception:
                    pass
                stdout_bytes, stderr_bytes = b"", b"Validation timed out"

            duration_ms = (time.perf_counter() - start_time) * 1000.0
            stdout_text = self._truncate_output(stdout_bytes.decode("utf-8", errors="replace"))
            stderr_text = self._truncate_output(stderr_bytes.decode("utf-8", errors="replace"))

            if timed_out:
                raise SandboxTimeoutError(f"Sandbox execution timed out after {timeout}s")

            return ValidationExecutionResult(
                exit_code=exit_code,
                stdout=stdout_text,
                stderr=stderr_text,
                duration_ms=round(duration_ms, 2),
                timed_out=False,
            )

        except SandboxTimeoutError:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ValidationExecutionResult(
                exit_code=None,
                stdout="",
                stderr=f"Execution timed out after {timeout}s",
                duration_ms=round(duration_ms, 2),
                timed_out=True,
            )

    def _truncate_output(self, text: str) -> str:
        """Truncates voluminous stdout/stderr output defensively."""
        if len(text) <= self.max_output_chars:
            return text
        half = self.max_output_chars // 2
        return text[:half] + "\n\n[... output truncated ...]\n\n" + text[-half:]
