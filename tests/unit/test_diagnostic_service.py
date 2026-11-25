from pathlib import Path
from typing import Any, TypeVar, cast

import pytest
from pydantic import BaseModel

from src.packages.sdk.llm_client import LLMAuthError, LLMTimeoutError
from src.packages.shared.diagnostic_service import DiagnosticService
from src.packages.shared.models import (
    CodeEvidence,
    DiagnosisProposal,
    EvidenceItem,
    EvidencePackage,
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


class _NoEvidenceResolver:
    """Explicit test double: diagnosis proceeds without repository evidence (no checkout)."""

    async def resolve_context(
        self,
        failure_context: FailureContext,
        repo_root: Path | None = None,
        provenance: Any = None,
        **_: object,
    ) -> None:
        return None


class _StaticEvidenceResolver:
    """Explicit test double returning a fixed, verified-shaped evidence package."""

    def __init__(self, package: EvidencePackage) -> None:
        self.package = package

    async def resolve_context(
        self,
        failure_context: FailureContext,
        repo_root: Path | None = None,
        provenance: Any = None,
        **_: object,
    ) -> EvidencePackage:
        return self.package


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

    service = DiagnosticService(
        context_resolver=cast(Any, _NoEvidenceResolver()),
        llm_client=MockLLMClient(proposal=expected_proposal),
    )
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
        context_resolver=cast(Any, _NoEvidenceResolver()),
        llm_client=MockLLMClient(raise_exc=LLMAuthError("Invalid API key", status_code=401)),
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
        context_resolver=cast(Any, _NoEvidenceResolver()),
        llm_client=MockLLMClient(raise_exc=LLMTimeoutError("Request timed out")),
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

    package = EvidencePackage(
        incident_id=sample_failure_context.incident_id,
        commit_sha=sample_failure_context.commit_sha,
        failure_context=sample_failure_context,
        code_evidences=[
            CodeEvidence(
                path="tests/test_math.py",
                start_line=18,
                end_line=22,
                target_line=20,
                content="20 | return a / b",
                total_file_lines=30,
            )
        ],
        retrieval_status="success",
    )
    service = DiagnosticService(
        context_resolver=cast(Any, _StaticEvidenceResolver(package)),
        llm_client=MockLLMClient(proposal=expected_proposal),
    )
    result = await service.diagnose_failure(sample_failure_context)

    assert result.evidence_package is not None
    assert result.proposal.confidence_score == 0.98
    assert result.human_review_required is True


# ---- Phase 1.9: supplied evidence is bound to the incident; resolution happens at most once ---


class _CountingResolver:
    def __init__(self, package: EvidencePackage | None = None) -> None:
        self.package = package
        self.calls = 0

    async def resolve_context(self, *args: Any, **kwargs: Any) -> EvidencePackage | None:
        self.calls += 1
        return self.package


def _evidence(base: FailureContext, **overrides: Any) -> EvidencePackage:
    bound = base.model_copy(update=overrides.pop("context", {}))
    values: dict[str, Any] = {
        "incident_id": base.incident_id,
        "commit_sha": base.commit_sha,
        "failure_context": bound,
        "retrieval_status": "success",
    }
    values.update(overrides)
    return EvidencePackage(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"incident_id": "inc_other"},
        {"commit_sha": "ffffffffffff"},
        {"context": {"incident_id": "inc_other"}},
        {"context": {"commit_sha": "ffffffffffff"}},
        {"context": {"repository_owner": "attacker"}},
        {"context": {"repository_name": "elsewhere"}},
    ],
)
async def test_supplied_evidence_for_other_work_fails_closed(
    overrides: dict[str, Any], sample_failure_context: FailureContext
) -> None:
    from src.packages.shared.persisted_provenance import ProvenanceRejected

    llm = MockLLMClient(raise_exc=AssertionError("LLM must not be called"))
    resolver = _CountingResolver()
    service = DiagnosticService(llm_client=llm, context_resolver=cast(Any, resolver))
    with pytest.raises(ProvenanceRejected) as rejected:
        await service.diagnose_failure(
            sample_failure_context, evidence_package=_evidence(sample_failure_context, **overrides)
        )
    assert rejected.value.code == "binding"
    assert resolver.calls == 0


@pytest.mark.asyncio
async def test_supplied_evidence_is_used_without_another_resolution(
    sample_failure_context: FailureContext,
) -> None:
    resolver = _CountingResolver()
    service = DiagnosticService(
        llm_client=MockLLMClient(raise_exc=LLMTimeoutError("slow")),
        context_resolver=cast(Any, resolver),
    )
    package = _evidence(sample_failure_context)
    result = await service.diagnose_failure(sample_failure_context, evidence_package=package)
    assert resolver.calls == 0
    assert result.incident_id == sample_failure_context.incident_id


@pytest.mark.asyncio
async def test_missing_evidence_still_resolves_exactly_once(
    sample_failure_context: FailureContext,
) -> None:
    resolver = _CountingResolver(package=None)  # resolver found nothing: diagnosis proceeds safely
    service = DiagnosticService(
        llm_client=MockLLMClient(raise_exc=LLMTimeoutError("slow")),
        context_resolver=cast(Any, resolver),
    )
    result = await service.diagnose_failure(sample_failure_context)
    assert resolver.calls == 1
    assert result.proposal.is_fixable is False  # fallback stays non-fixable
