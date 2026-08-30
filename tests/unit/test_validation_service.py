from pathlib import Path

import pytest

from src.packages.sdk.repo_checkout import RepoCheckoutProtocol, RepositoryCheckoutError
from src.packages.sdk.sandbox_runner import (
    DockerUnavailableError,
    PatchApplicationError,
    SandboxRunnerProtocol,
    SandboxTimeoutError,
    ValidationExecutionResult,
)
from src.packages.shared.models import (
    FailureCategory,
    FailureContext,
    FailureSignal,
    FixProposal,
    ValidationCommand,
    ValidationStatus,
    WorkflowRunConclusion,
)
from src.packages.shared.validation_service import ValidationService


class MockSandboxRunner(SandboxRunnerProtocol):
    """Mock sandbox runner returning predetermined results or raising exceptions."""

    def __init__(
        self,
        result: ValidationExecutionResult | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.result = result
        self.raise_exc = raise_exc

    async def run_validation(
        self,
        repo_source_dir: Path,
        patch_diff: str,
        command: ValidationCommand,
        timeout_seconds: float | None = None,
    ) -> ValidationExecutionResult:
        if self.raise_exc:
            raise self.raise_exc
        if self.result:
            return self.result
        return ValidationExecutionResult(
            exit_code=0,
            stdout="OK",
            stderr="",
            duration_ms=50.0,
            timed_out=False,
        )


class MockCheckoutManager(RepoCheckoutProtocol):
    """Mock checkout manager."""

    def __init__(self, raise_exc: Exception | None = None) -> None:
        self.raise_exc = raise_exc

    def checkout_commit(
        self, repo_owner: str, repo_name: str, commit_sha: str, clone_url: str | None = None
    ) -> Path:
        if self.raise_exc:
            raise self.raise_exc
        return Path("/tmp/mock_repo")


@pytest.fixture
def sample_proposal_and_context(tmp_path: Path) -> tuple[FixProposal, FailureContext]:
    context = FailureContext(
        incident_id="inc_val_test_01",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=555,
        workflow_name="CI",
        commit_sha="112233445566",
        branch="main",
        run_url="https://ci",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=FailureCategory.TEST,
            error_type="ZeroDivisionError",
            message="err",
            target_file="src/calc.py",
            target_line=1,
            extracted_snippet="err",
        ),
        raw_log_excerpt="err",
    )

    proposal = FixProposal(
        proposal_id="fix_inc_val_test_01_11223344",
        incident_id="inc_val_test_01",
        diagnosis_id="diag_01",
        commit_sha="112233445566",
        status="proposed",
        is_valid=True,
        unified_diff=(
            "--- a/src/calc.py\n+++ b/src/calc.py\n@@ -1,1 +1,2 @@\n def div():\n+ return 0\n"
        ),
        target_files=["src/calc.py"],
        rationale="Fix division",
        assumptions=[],
        risk_level="low",
        has_dependency_changes=False,
        confidence_score=0.95,
    )
    return proposal, context


def test_command_selection_matrix(
    sample_proposal_and_context: tuple[FixProposal, FailureContext],
) -> None:
    _, context = sample_proposal_and_context
    service = ValidationService()

    # Test
    context.signal.category = FailureCategory.TEST
    assert service.select_validation_command(context) == ValidationCommand.PYTEST

    # Lint (Ruff)
    context.signal.category = FailureCategory.LINT
    context.signal.error_type = "F401"
    assert service.select_validation_command(context) == ValidationCommand.RUFF

    # Lint (Mypy)
    context.signal.error_type = "TypeError"
    assert service.select_validation_command(context) == ValidationCommand.MYPY

    # Dependency / Unknown
    context.signal.category = FailureCategory.DEPENDENCY
    assert service.select_validation_command(context) == ValidationCommand.PYTHON_SYNTAX


@pytest.mark.asyncio
async def test_validation_service_invalid_proposal(
    sample_proposal_and_context: tuple[FixProposal, FailureContext],
) -> None:
    proposal, context = sample_proposal_and_context
    proposal.is_valid = False

    service = ValidationService()
    result = await service.validate_fix(proposal, context)

    assert result.status == ValidationStatus.PATCH_REJECTED
    assert result.failure_reason is not None


@pytest.mark.asyncio
async def test_validation_service_checkout_failure(
    sample_proposal_and_context: tuple[FixProposal, FailureContext],
) -> None:
    proposal, context = sample_proposal_and_context
    mock_checkout = MockCheckoutManager(
        raise_exc=RepositoryCheckoutError("Commit SHA 1122 not found")
    )

    service = ValidationService(checkout_manager=mock_checkout)
    result = await service.validate_fix(proposal, context, repo_root=None)

    assert result.status == ValidationStatus.INFRASTRUCTURE_ERROR
    assert "Commit SHA 1122 not found" in result.stderr


@pytest.mark.asyncio
async def test_validation_service_passed(
    sample_proposal_and_context: tuple[FixProposal, FailureContext],
    tmp_path: Path,
) -> None:
    proposal, context = sample_proposal_and_context
    mock_runner = MockSandboxRunner(
        result=ValidationExecutionResult(
            exit_code=0,
            stdout="1 passed",
            stderr="",
            duration_ms=120.0,
            timed_out=False,
        )
    )

    service = ValidationService(sandbox_runner=mock_runner)
    result = await service.validate_fix(proposal, context, repo_root=tmp_path)

    assert result.status == ValidationStatus.PASSED
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.command_executed == "pytest"
    assert "1 passed" in result.stdout


@pytest.mark.asyncio
async def test_validation_service_failed(
    sample_proposal_and_context: tuple[FixProposal, FailureContext],
    tmp_path: Path,
) -> None:
    proposal, context = sample_proposal_and_context
    mock_runner = MockSandboxRunner(
        result=ValidationExecutionResult(
            exit_code=1,
            stdout="1 failed",
            stderr="AssertionError",
            duration_ms=150.0,
            timed_out=False,
        )
    )

    service = ValidationService(sandbox_runner=mock_runner)
    result = await service.validate_fix(proposal, context, repo_root=tmp_path)

    assert result.status == ValidationStatus.FAILED
    assert result.exit_code == 1
    assert "AssertionError" in result.stderr


@pytest.mark.asyncio
async def test_validation_service_patch_rejected(
    sample_proposal_and_context: tuple[FixProposal, FailureContext],
    tmp_path: Path,
) -> None:
    proposal, context = sample_proposal_and_context
    mock_runner = MockSandboxRunner(
        raise_exc=PatchApplicationError("git apply rejected patch: hunk failed")
    )

    service = ValidationService(sandbox_runner=mock_runner)
    result = await service.validate_fix(proposal, context, repo_root=tmp_path)

    assert result.status == ValidationStatus.PATCH_REJECTED
    assert "hunk failed" in result.stderr


@pytest.mark.asyncio
async def test_validation_service_timeout(
    sample_proposal_and_context: tuple[FixProposal, FailureContext],
    tmp_path: Path,
) -> None:
    proposal, context = sample_proposal_and_context
    mock_runner = MockSandboxRunner(raise_exc=SandboxTimeoutError("Timed out after 30s"))

    service = ValidationService(sandbox_runner=mock_runner)
    result = await service.validate_fix(proposal, context, repo_root=tmp_path)

    assert result.status == ValidationStatus.TIMED_OUT
    assert result.timed_out is True


@pytest.mark.asyncio
async def test_validation_service_docker_unavailable(
    sample_proposal_and_context: tuple[FixProposal, FailureContext],
    tmp_path: Path,
) -> None:
    proposal, context = sample_proposal_and_context
    mock_runner = MockSandboxRunner(raise_exc=DockerUnavailableError("Docker daemon offline"))

    service = ValidationService(sandbox_runner=mock_runner)
    result = await service.validate_fix(proposal, context, repo_root=tmp_path)

    assert result.status == ValidationStatus.INFRASTRUCTURE_ERROR
    assert "Docker daemon offline" in result.stderr


@pytest.mark.asyncio
async def test_validation_service_unexpected_error(
    sample_proposal_and_context: tuple[FixProposal, FailureContext],
    tmp_path: Path,
) -> None:
    proposal, context = sample_proposal_and_context
    mock_runner = MockSandboxRunner(raise_exc=RuntimeError("Unexpected OS crash"))

    service = ValidationService(sandbox_runner=mock_runner)
    result = await service.validate_fix(proposal, context, repo_root=tmp_path)

    assert result.status == ValidationStatus.INFRASTRUCTURE_ERROR
    assert result.failure_reason is not None
    assert "Unexpected validation error" in result.failure_reason
