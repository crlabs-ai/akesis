import time
from pathlib import Path

from src.packages.sdk.repo_checkout import (
    GitRepositoryCheckoutManager,
    RepoCheckoutProtocol,
    RepositoryCheckoutError,
)
from src.packages.sdk.sandbox_runner import (
    DockerSandboxRunner,
    DockerUnavailableError,
    PatchApplicationError,
    SandboxRunnerProtocol,
    SandboxTimeoutError,
)
from src.packages.shared.logging import get_logger
from src.packages.shared.models import (
    FailureCategory,
    FailureContext,
    FixProposal,
    ValidationCommand,
    ValidationResult,
    ValidationStatus,
)

logger = get_logger("akesis.validation_service")


class ValidationService:
    """Orchestrates deterministic command mapping and isolated sandbox validation."""

    def __init__(
        self,
        sandbox_runner: SandboxRunnerProtocol | None = None,
        checkout_manager: RepoCheckoutProtocol | None = None,
    ) -> None:
        self.sandbox_runner = sandbox_runner or DockerSandboxRunner()
        self.checkout_manager = checkout_manager or GitRepositoryCheckoutManager()

    def select_validation_command(self, context: FailureContext) -> ValidationCommand | None:
        """Maps FailureContext deterministically to an allowlisted ValidationCommand."""
        signal = context.signal
        cat = signal.category
        err_type = (signal.error_type or "").lower()

        if cat == FailureCategory.LINT:
            if "type" in err_type or "mypy" in err_type:
                return ValidationCommand.MYPY
            return ValidationCommand.RUFF

        if cat == FailureCategory.TEST:
            return ValidationCommand.PYTEST

        if cat in (FailureCategory.DEPENDENCY, FailureCategory.UNKNOWN, FailureCategory.BUILD):
            return ValidationCommand.PYTHON_SYNTAX

        return None

    async def validate_fix(
        self,
        proposal: FixProposal,
        context: FailureContext,
        repo_root: Path | None = None,
    ) -> ValidationResult:
        """Executes end-to-end sandbox validation for a FixProposal."""
        start_time = time.perf_counter()
        validation_id = f"val_{proposal.proposal_id}"
        incident_id = context.incident_id
        commit_sha = proposal.commit_sha

        logger.info(
            "validation_started",
            validation_id=validation_id,
            proposal_id=proposal.proposal_id,
            commit_sha=commit_sha,
            is_valid_proposal=proposal.is_valid,
        )

        # 1. Reject invalid or ineligible proposals immediately
        if not proposal.is_valid or proposal.status != "proposed" or not proposal.unified_diff:
            return ValidationResult(
                validation_id=validation_id,
                proposal_id=proposal.proposal_id,
                incident_id=incident_id,
                commit_sha=commit_sha,
                status=ValidationStatus.PATCH_REJECTED,
                command_executed="none",
                exit_code=None,
                stdout="",
                stderr="Proposal is invalid or lacks a valid unified diff patch.",
                duration_ms=0.0,
                timed_out=False,
                failure_reason="FixProposal is not in valid proposed state.",
            )

        # 2. Select deterministic allowlisted command
        command = self.select_validation_command(context)
        if not command:
            return ValidationResult(
                validation_id=validation_id,
                proposal_id=proposal.proposal_id,
                incident_id=incident_id,
                commit_sha=commit_sha,
                status=ValidationStatus.UNSUPPORTED,
                command_executed="none",
                exit_code=None,
                stdout="",
                stderr="No deterministic validation command mapped for this failure category.",
                duration_ms=0.0,
                timed_out=False,
                failure_reason="Unsupported validation category.",
            )

        # 3. Resolve exact commit checkout directory
        if repo_root is None:
            try:
                repo_root = self.checkout_manager.checkout_commit(
                    repo_owner=context.repository_owner,
                    repo_name=context.repository_name,
                    commit_sha=commit_sha,
                )
            except RepositoryCheckoutError as err:
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return ValidationResult(
                    validation_id=validation_id,
                    proposal_id=proposal.proposal_id,
                    incident_id=incident_id,
                    commit_sha=commit_sha,
                    status=ValidationStatus.INFRASTRUCTURE_ERROR,
                    command_executed=str(command),
                    exit_code=None,
                    stdout="",
                    stderr=f"Repository checkout failed: {err}",
                    duration_ms=round(duration_ms, 2),
                    timed_out=False,
                    failure_reason="Failed to checkout exact commit SHA.",
                )

        # 4. Invocate Sandbox Runner
        try:
            exec_res = await self.sandbox_runner.run_validation(
                repo_source_dir=repo_root,
                patch_diff=proposal.unified_diff,
                command=command,
            )

            status = (
                ValidationStatus.TIMED_OUT
                if exec_res.timed_out
                else (
                    ValidationStatus.PASSED if exec_res.exit_code == 0 else ValidationStatus.FAILED
                )
            )

            result = ValidationResult(
                validation_id=validation_id,
                proposal_id=proposal.proposal_id,
                incident_id=incident_id,
                commit_sha=commit_sha,
                status=status,
                command_executed=str(command),
                exit_code=exec_res.exit_code,
                stdout=exec_res.stdout,
                stderr=exec_res.stderr,
                duration_ms=exec_res.duration_ms,
                timed_out=exec_res.timed_out,
                failure_reason=(
                    None if status == ValidationStatus.PASSED else f"Exit code {exec_res.exit_code}"
                ),
            )

            logger.info(
                "validation_completed",
                validation_id=validation_id,
                status=status,
                exit_code=exec_res.exit_code,
                duration_ms=exec_res.duration_ms,
            )
            return result

        except PatchApplicationError as err:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ValidationResult(
                validation_id=validation_id,
                proposal_id=proposal.proposal_id,
                incident_id=incident_id,
                commit_sha=commit_sha,
                status=ValidationStatus.PATCH_REJECTED,
                command_executed=str(command),
                exit_code=None,
                stdout="",
                stderr=str(err),
                duration_ms=round(duration_ms, 2),
                timed_out=False,
                failure_reason="Patch rejected by git apply.",
            )

        except SandboxTimeoutError as err:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ValidationResult(
                validation_id=validation_id,
                proposal_id=proposal.proposal_id,
                incident_id=incident_id,
                commit_sha=commit_sha,
                status=ValidationStatus.TIMED_OUT,
                command_executed=str(command),
                exit_code=None,
                stdout="",
                stderr=str(err),
                duration_ms=round(duration_ms, 2),
                timed_out=True,
                failure_reason="Validation container execution timed out.",
            )

        except DockerUnavailableError as err:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ValidationResult(
                validation_id=validation_id,
                proposal_id=proposal.proposal_id,
                incident_id=incident_id,
                commit_sha=commit_sha,
                status=ValidationStatus.INFRASTRUCTURE_ERROR,
                command_executed=str(command),
                exit_code=None,
                stdout="",
                stderr=str(err),
                duration_ms=round(duration_ms, 2),
                timed_out=False,
                failure_reason="Docker daemon unavailable on host.",
            )

        except Exception as err:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ValidationResult(
                validation_id=validation_id,
                proposal_id=proposal.proposal_id,
                incident_id=incident_id,
                commit_sha=commit_sha,
                status=ValidationStatus.INFRASTRUCTURE_ERROR,
                command_executed=str(command),
                exit_code=None,
                stdout="",
                stderr=str(err),
                duration_ms=round(duration_ms, 2),
                timed_out=False,
                failure_reason=f"Unexpected validation error: {err}",
            )
