from typing import TypeVar

import pytest
from pydantic import BaseModel

from src.packages.sdk.llm_client import LLMAuthError, LLMTimeoutError
from src.packages.shared.diagnostic_service import DiagnosticService
from src.packages.shared.models import (
    DiagnosisProposal,
    EvidenceItem,
    FailureCategory,
    FailureContext,
    FailureSignal,
    RemediationDirection,
    WorkflowRunConclusion,
)

T = TypeVar("T", bound=BaseModel)


class MockLLMClient:
    """In-memory fake LLM client for provider-agnostic testing."""

    def __init__(
        self,
        proposal: DiagnosisProposal | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.proposal = proposal
        self.raise_exc = raise_exc
        self.model_name = "mock-gemini-client"

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[T],
        system_instruction: str | None = None,
        temperature: float = 0.0,
    ) -> T:
        if self.raise_exc:
            raise self.raise_exc
        if self.proposal:
            return self.proposal  # type: ignore[return-value]
        raise ValueError("No mock proposal configured")


@pytest.fixture
def sample_failure_context() -> FailureContext:
    return FailureContext(
        incident_id="inc_diag_test_01",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=987654,
        workflow_name="CI / Tests",
        commit_sha="112233445566",
        branch="main",
        run_url="https://github.com/crlabs-ai/akesis/actions/runs/987654",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=FailureCategory.TEST,
            error_type="ZeroDivisionError",
            message="division by zero",
            target_file="tests/test_math.py",
            target_line=20,
            extracted_snippet="ZeroDivisionError: division by zero",
        ),
        raw_log_excerpt="ZeroDivisionError: division by zero",
    )


@pytest.mark.asyncio
async def test_diagnostic_service_success(sample_failure_context: FailureContext) -> None:
    expected_proposal = DiagnosisProposal(
        category=FailureCategory.TEST,
        root_cause="Division by zero in test_math.py",
        evidence=[
            EvidenceItem(
                source="log_traceback",
                observation="ZeroDivisionError on line 20",
                file_path="tests/test_math.py",
                line_number=20,
            )
        ],
        target_file="tests/test_math.py",
        target_line=20,
        remediation_direction=RemediationDirection(
            summary="Prevent zero division",
            suggested_action="Add input validation",
            risk_assessment="Minimal risk",
        ),
        is_fixable=True,
        confidence_score=0.95,
        evidence_sufficiency="sufficient",
        reasoning="Direct traceback evidence",
    )

    service = DiagnosticService(llm_client=MockLLMClient(proposal=expected_proposal))
    result = await service.diagnose_failure(sample_failure_context)

    assert result.incident_id == "inc_diag_test_01"
    assert result.proposal.category == FailureCategory.TEST
    assert result.proposal.confidence_score == 0.95
    assert result.human_review_required is True  # Mandatory invariant
    assert result.model_name == "mock-gemini-client"
    assert result.execution_time_ms >= 0.0


@pytest.mark.asyncio
async def test_diagnostic_service_fallback_on_auth_error(
    sample_failure_context: FailureContext,
) -> None:
    service = DiagnosticService(
        llm_client=MockLLMClient(raise_exc=LLMAuthError("Invalid API key", status_code=401))
    )
    result = await service.diagnose_failure(sample_failure_context)

    assert result.incident_id == "inc_diag_test_01"
    assert result.proposal.evidence_sufficiency == "insufficient"
    assert result.proposal.confidence_score == 0.0
    assert result.human_review_required is True
    assert result.model_name == "deterministic-fallback"
    assert "LLM Provider Error" in result.proposal.root_cause


@pytest.mark.asyncio
async def test_diagnostic_service_fallback_on_timeout(
    sample_failure_context: FailureContext,
) -> None:
    service = DiagnosticService(
        llm_client=MockLLMClient(raise_exc=LLMTimeoutError("Request timed out"))
    )
    result = await service.diagnose_failure(sample_failure_context)

    assert result.proposal.confidence_score == 0.0
    assert result.human_review_required is True
    assert result.model_name == "deterministic-fallback"


@pytest.mark.asyncio
async def test_diagnostic_service_with_codebase_evidence(
    sample_failure_context: FailureContext,
) -> None:
    expected_proposal = DiagnosisProposal(
        category=FailureCategory.TEST,
        root_cause="Division by zero in test_math.py",
        evidence=[
            EvidenceItem(
                source="source_code",
                observation="Zero division line in code snippet",
                file_path="tests/test_math.py",
                line_number=20,
            )
        ],
        target_file="tests/test_math.py",
        target_line=20,
        remediation_direction=RemediationDirection(
            summary="Prevent zero division",
            suggested_action="Add input validation",
            risk_assessment="Minimal risk",
        ),
        is_fixable=True,
        confidence_score=0.98,
        evidence_sufficiency="sufficient",
        reasoning="Traceback matches verified code evidence.",
    )

    service = DiagnosticService(llm_client=MockLLMClient(proposal=expected_proposal))
    result = await service.diagnose_failure(sample_failure_context)

    assert result.evidence_package is not None
    assert result.proposal.confidence_score == 0.98
    assert result.human_review_required is True
