from typing import TypeVar

import pytest
from pydantic import BaseModel

from src.packages.shared.diagnostic_service import DiagnosticService
from src.packages.shared.models import (
    DiagnosisProposal,
    DiagnosticResult,
    EvidenceItem,
    FailureCategory,
    FailureContext,
    FailureSignal,
    RemediationDirection,
    TracebackFrame,
    WorkflowRunConclusion,
)

T = TypeVar("T", bound=BaseModel)


class MockLLMClient:
    """Mock LLM client returning a predefined proposal."""

    def __init__(self, proposal: DiagnosisProposal) -> None:
        self.proposal = proposal
        self.model_name = "gemini-1.5-flash-mock"

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[T],
        system_instruction: str | None = None,
        temperature: float = 0.0,
    ) -> T:
        return self.proposal  # type: ignore[return-value]


def create_context(
    category: FailureCategory,
    error_type: str,
    message: str,
    file_path: str | None = None,
    line_no: int | None = None,
    snippet: str = "",
    frames: list[TracebackFrame] | None = None,
) -> FailureContext:
    return FailureContext(
        incident_id=f"inc_scenario_{category}",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=123456,
        workflow_name="CI / Triage Test",
        commit_sha="abcdef987654",
        branch="feat/diagnostic-baseline",
        run_url="https://github.com/crlabs-ai/akesis/actions/runs/123456",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=category,
            error_type=error_type,
            message=message,
            target_file=file_path,
            target_line=line_no,
            extracted_snippet=snippet,
            traceback_frames=frames or [],
        ),
        raw_log_excerpt=snippet,
    )


# ==============================================================================
# 1. Dependency Resolution Failure
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_dependency_failure() -> None:
    context = create_context(
        category=FailureCategory.DEPENDENCY,
        error_type="DependencyResolutionError",
        message="Resolution failed for package: pydantic-core",
        snippet="ERROR: ResolutionImpossible: for package 'pydantic-core'",
    )
    proposal = DiagnosisProposal(
        category=FailureCategory.DEPENDENCY,
        root_cause="Incompatible dependency constraints for package 'pydantic-core'",
        evidence=[
            EvidenceItem(
                source="pip_resolver",
                observation="ResolutionImpossible: for package 'pydantic-core'",
            )
        ],
        target_file="pyproject.toml",
        target_line=None,
        remediation_direction=RemediationDirection(
            summary="Relax pydantic-core version constraints",
            suggested_action="Update dependency requirement in pyproject.toml",
            risk_assessment="Low; verify compatibility with other packages",
        ),
        is_fixable=True,
        confidence_score=0.92,
        evidence_sufficiency="sufficient",
        reasoning="Pip solver reported resolution conflict on pydantic-core.",
    )

    service = DiagnosticService(llm_client=MockLLMClient(proposal))
    result: DiagnosticResult = await service.diagnose_failure(context)

    assert result.proposal.category == FailureCategory.DEPENDENCY
    assert result.proposal.is_fixable is True
    assert result.proposal.confidence_score >= 0.85
    assert result.human_review_required is True


# ==============================================================================
# 2. ModuleNotFoundError
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_module_not_found() -> None:
    context = create_context(
        category=FailureCategory.DEPENDENCY,
        error_type="ModuleNotFoundError",
        message="No module named 'non_existent_package'",
        file_path="src/apps/api/main.py",
        line_no=4,
        snippet="ModuleNotFoundError: No module named 'non_existent_package'",
    )
    proposal = DiagnosisProposal(
        category=FailureCategory.DEPENDENCY,
        root_cause="Missing package 'non_existent_package' imported in main.py",
        evidence=[
            EvidenceItem(
                source="python_runtime",
                observation="ModuleNotFoundError: No module named 'non_existent_package'",
                file_path="src/apps/api/main.py",
                line_number=4,
            )
        ],
        target_file="src/apps/api/main.py",
        target_line=4,
        remediation_direction=RemediationDirection(
            summary="Install package or remove import statement",
            suggested_action="Add dependency to pyproject.toml or remove invalid import",
            risk_assessment="Low risk; single file modification",
        ),
        is_fixable=True,
        confidence_score=0.96,
        evidence_sufficiency="sufficient",
        reasoning="Direct traceback to line 4 attempting to import missing module.",
    )

    service = DiagnosticService(llm_client=MockLLMClient(proposal))
    result = await service.diagnose_failure(context)

    assert result.proposal.category == FailureCategory.DEPENDENCY
    assert result.proposal.target_file == "src/apps/api/main.py"
    assert result.proposal.target_line == 4
    assert result.proposal.is_fixable is True


# ==============================================================================
# 3. Ruff Lint Failure
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_ruff_lint_failure() -> None:
    context = create_context(
        category=FailureCategory.LINT,
        error_type="LintViolation(F401)",
        message="`os` imported but unused",
        file_path="src/packages/shared/utils.py",
        line_no=24,
        snippet="src/packages/shared/utils.py:24:5: F401 `os` imported but unused",
    )
    proposal = DiagnosisProposal(
        category=FailureCategory.LINT,
        root_cause="Unused import 'os' violating rule F401",
        evidence=[
            EvidenceItem(
                source="ruff_linter",
                observation="src/packages/shared/utils.py:24:5: F401 `os` imported but unused",
                file_path="src/packages/shared/utils.py",
                line_number=24,
            )
        ],
        target_file="src/packages/shared/utils.py",
        target_line=24,
        remediation_direction=RemediationDirection(
            summary="Remove unused import",
            suggested_action="Delete line 24 'import os'",
            risk_assessment="Zero risk",
        ),
        is_fixable=True,
        confidence_score=0.99,
        evidence_sufficiency="sufficient",
        reasoning="Deterministic linter rule code F401 with exact file and line.",
    )

    service = DiagnosticService(llm_client=MockLLMClient(proposal))
    result = await service.diagnose_failure(context)

    assert result.proposal.category == FailureCategory.LINT
    assert result.proposal.is_fixable is True
    assert result.proposal.confidence_score >= 0.95


# ==============================================================================
# 4. Mypy Type Error
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_mypy_type_failure() -> None:
    context = create_context(
        category=FailureCategory.LINT,
        error_type="TypeError",
        message="Incompatible return value type (got 'int', expected 'str')",
        file_path="src/packages/sdk/client.py",
        line_no=45,
        snippet="src/packages/sdk/client.py:45: error: Incompatible return value type",
    )
    proposal = DiagnosisProposal(
        category=FailureCategory.LINT,
        root_cause="Type mismatch in return value: expected str but returned int",
        evidence=[
            EvidenceItem(
                source="mypy_typechecker",
                observation="Incompatible return value type (got 'int', expected 'str')",
                file_path="src/packages/sdk/client.py",
                line_number=45,
            )
        ],
        target_file="src/packages/sdk/client.py",
        target_line=45,
        remediation_direction=RemediationDirection(
            summary="Cast or convert return value to string",
            suggested_action="Wrap return value in str() or update return type annotation",
            risk_assessment="Low risk; type constraint enforcement",
        ),
        is_fixable=True,
        confidence_score=0.94,
        evidence_sufficiency="sufficient",
        reasoning="Mypy reported type mismatch on client function return.",
    )

    service = DiagnosticService(llm_client=MockLLMClient(proposal))
    result = await service.diagnose_failure(context)

    assert result.proposal.category == FailureCategory.LINT
    assert result.proposal.target_file == "src/packages/sdk/client.py"


# ==============================================================================
# 5. Pytest Assertion Failure
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_pytest_assertion_failure() -> None:
    context = create_context(
        category=FailureCategory.TEST,
        error_type="ZeroDivisionError",
        message="division by zero",
        file_path="tests/test_calculator.py",
        line_no=18,
        snippet="FAILED tests/test_calculator.py::test_divide - ZeroDivisionError: by zero",
    )
    proposal = DiagnosisProposal(
        category=FailureCategory.TEST,
        root_cause="ZeroDivisionError encountered during division test execution",
        evidence=[
            EvidenceItem(
                source="pytest_runner",
                observation="ZeroDivisionError: division by zero in test_divide",
                file_path="tests/test_calculator.py",
                line_number=18,
            )
        ],
        target_file="tests/test_calculator.py",
        target_line=18,
        remediation_direction=RemediationDirection(
            summary="Catch zero division or validate inputs",
            suggested_action="Add defensive check for denominator zero in calculation module",
            risk_assessment="Low risk",
        ),
        is_fixable=True,
        confidence_score=0.95,
        evidence_sufficiency="sufficient",
        reasoning="Test failed explicitly on unhandled zero division exception.",
    )

    service = DiagnosticService(llm_client=MockLLMClient(proposal))
    result = await service.diagnose_failure(context)

    assert result.proposal.category == FailureCategory.TEST
    assert result.proposal.target_line == 18


# ==============================================================================
# 6. Generic Shell / Build Failure
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_generic_shell_failure() -> None:
    context = create_context(
        category=FailureCategory.UNKNOWN,
        error_type="GenericCIError",
        message="gcc command not found",
        snippet="FATAL: command failed: gcc -o output main.c\nExit code 127: gcc command not found",
    )
    proposal = DiagnosisProposal(
        category=FailureCategory.BUILD,
        root_cause="Missing build dependency: GCC compiler not installed on runner",
        evidence=[
            EvidenceItem(
                source="shell_stderr",
                observation="gcc command not found",
            )
        ],
        target_file=None,
        target_line=None,
        remediation_direction=RemediationDirection(
            summary="Install build-essential in CI environment",
            suggested_action="Add apt-get install -y gcc to workflow definition",
            risk_assessment="CI configuration change; test on workflow branch",
        ),
        is_fixable=True,
        confidence_score=0.88,
        evidence_sufficiency="sufficient",
        reasoning="Binary gcc missing from PATH in container runner.",
    )

    service = DiagnosticService(llm_client=MockLLMClient(proposal))
    result = await service.diagnose_failure(context)

    assert result.proposal.category == FailureCategory.BUILD
    assert result.proposal.is_fixable is True


# ==============================================================================
# 7. Ambiguous / Insufficient Evidence Case
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_ambiguous_insufficient_evidence() -> None:
    context = create_context(
        category=FailureCategory.UNKNOWN,
        error_type="UnclassifiedFailure",
        message="Non-zero exit code detected",
        snippet="Process completed with exit code 1.",
    )
    proposal = DiagnosisProposal(
        category=FailureCategory.UNKNOWN,
        root_cause="Insufficient error detail captured in logs",
        evidence=[
            EvidenceItem(
                source="ci_exit_status",
                observation="Process completed with exit code 1 without error text",
            )
        ],
        target_file=None,
        target_line=None,
        remediation_direction=RemediationDirection(
            summary="Enable verbose CI logging",
            suggested_action="Run CI with DEBUG=1 to capture diagnostic trace",
            risk_assessment="None",
        ),
        is_fixable=False,
        confidence_score=0.15,
        evidence_sufficiency="insufficient",
        reasoning="No traceback, syntax error, or tool error output was recorded.",
    )

    service = DiagnosticService(llm_client=MockLLMClient(proposal))
    result = await service.diagnose_failure(context)

    assert result.proposal.evidence_sufficiency == "insufficient"
    assert result.proposal.confidence_score <= 0.3
    assert result.proposal.is_fixable is False
