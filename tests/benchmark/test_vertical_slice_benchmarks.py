from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.packages.database.repositories import (
    ApprovalRepository,
    ApprovalRepositoryProtocol,
    MutationRepository,
    MutationRepositoryProtocol,
    PipelineRepository,
    PipelineRepositoryProtocol,
)
from src.packages.shared.config import settings
from src.packages.shared.models import (
    ApprovalRecord,
    ApprovalStatus,
    CodeEvidence,
    DiagnosisProposal,
    DiagnosticResult,
    EvidenceItem,
    EvidencePackage,
    FailureCategory,
    FailureContext,
    FailureSignal,
    FixProposal,
    MutationRecord,
    MutationStatus,
    PipelineStatus,
    RemediationDirection,
    ValidationResult,
    ValidationStatus,
    WorkflowRunConclusion,
)
from src.packages.shared.mutation_service import StaleCommitError
from src.packages.shared.remediation_orchestrator import (
    OrchestrationError,
    RemediationOrchestrator,
)


class BenchmarkDiagService:
    def __init__(
        self,
        category: FailureCategory = FailureCategory.TEST,
        is_fixable: bool = True,
        confidence_score: float = 0.95,
        target_file: str = "src/calculator.py",
        root_cause: str = "Logic error in calculation",
    ) -> None:
        self.category = category
        self.is_fixable = is_fixable
        self.confidence_score = confidence_score
        self.target_file = target_file
        self.root_cause = root_cause

    async def diagnose_failure(
        self, context: FailureContext, evidence_package: EvidencePackage | None = None
    ) -> DiagnosticResult:
        proposal = DiagnosisProposal(
            category=self.category,
            root_cause=self.root_cause,
            evidence=[
                EvidenceItem(
                    source="ci_log",
                    observation=f"Failure in {self.target_file}: {self.root_cause}",
                    file_path=self.target_file,
                    line_number=10,
                )
            ],
            target_file=self.target_file,
            target_line=10,
            remediation_direction=RemediationDirection(
                summary="Apply verified fix",
                suggested_action=f"Correct line in {self.target_file}",
                risk_assessment="Low risk isolated change",
            ),
            is_fixable=self.is_fixable,
            confidence_score=self.confidence_score,
            evidence_sufficiency="sufficient",
            reasoning="Deterministic evidence supports targeted correction.",
        )
        return DiagnosticResult(
            incident_id=context.incident_id,
            proposal=proposal,
            model_name="gemini-2.5-pro",
            execution_time_ms=35.0,
        )


class BenchmarkContextResolver:
    def __init__(self, target_file: str = "src/calculator.py") -> None:
        self.target_file = target_file

    def resolve_context(
        self, failure_context: FailureContext, repo_root: Any = None
    ) -> EvidencePackage:
        return EvidencePackage(
            incident_id=failure_context.incident_id,
            commit_sha=failure_context.commit_sha,
            failure_context=failure_context,
            code_evidences=[
                CodeEvidence(
                    path=self.target_file,
                    start_line=1,
                    end_line=20,
                    content="def calculate(a, b):\n    return a + b\n",
                    total_file_lines=20,
                )
            ],
            retrieval_status="success",
        )


class BenchmarkFixService:
    def __init__(
        self,
        is_valid: bool = True,
        status: str = "proposed",
        target_file: str = "src/calculator.py",
        diff: str = (
            "--- a/src/calculator.py\n+++ b/src/calculator.py\n@@ -1,2 +1,2 @@\n"
            "-def calculate(a, b): return a - b\n+def calculate(a, b): return a + b\n"
        ),
        rejection_reasons: list[str] | None = None,
    ) -> None:
        self.is_valid = is_valid
        self.status = status
        self.target_file = target_file
        self.diff = diff
        self.rejection_reasons = rejection_reasons or []

    async def generate_fix_proposal(
        self,
        context: FailureContext,
        evidence_package: EvidencePackage,
        diagnostic_result: DiagnosticResult | None = None,
    ) -> FixProposal:
        return FixProposal(
            proposal_id=f"prop_{context.incident_id}",
            incident_id=context.incident_id,
            commit_sha=context.commit_sha,
            status=self.status,  # type: ignore[arg-type]
            is_valid=self.is_valid,
            rejection_reasons=self.rejection_reasons,
            unified_diff=self.diff,
            target_files=[self.target_file],
            rationale="Correct calculation logic",
            risk_level="low",
            confidence_score=0.95,
        )


class BenchmarkValidationService:
    def __init__(
        self,
        status: ValidationStatus = ValidationStatus.PASSED,
        exit_code: int = 0,
        stderr: str = "",
    ) -> None:
        self.status = status
        self.exit_code = exit_code
        self.stderr = stderr

    async def validate_fix(
        self, proposal: FixProposal, context: FailureContext, repo_root: Any = None
    ) -> ValidationResult:
        return ValidationResult(
            validation_id=f"val_{proposal.proposal_id}",
            proposal_id=proposal.proposal_id,
            incident_id=context.incident_id,
            commit_sha=context.commit_sha,
            status=self.status,
            command_executed="pytest",
            exit_code=self.exit_code,
            stderr=self.stderr,
            duration_ms=25.0,
        )


class BenchmarkApprovalService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def request_approval(
        self, context: FailureContext, proposal: FixProposal, validation: ValidationResult
    ) -> ApprovalRecord:
        record = ApprovalRecord(
            approval_id=f"appr_{proposal.proposal_id}",
            incident_id=context.incident_id,
            proposal_id=proposal.proposal_id,
            commit_sha=context.commit_sha,
            status=ApprovalStatus.PENDING,
        )
        async with self.session_factory() as session:
            repo = ApprovalRepository(session)
            return await repo.create_approval(record)


class BenchmarkMutationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        stale_sha: bool = False,
        should_fail: bool = False,
    ) -> None:
        self.session_factory = session_factory
        self.stale_sha = stale_sha
        self.should_fail = should_fail
        self.mutation_invoked = False
        self.last_proposal: FixProposal | None = None

    async def create_pull_request(
        self,
        context: FailureContext,
        proposal: FixProposal,
        validation: ValidationResult,
        approval: ApprovalRecord,
    ) -> MutationRecord:
        self.mutation_invoked = True
        self.last_proposal = proposal
        if self.stale_sha:
            raise StaleCommitError("Remote HEAD sha does not match proposal commit sha")
        if self.should_fail:
            from src.packages.shared.mutation_service import MutationError

            raise MutationError("Git push permission rejected")

        record = MutationRecord(
            mutation_id=f"mut_{proposal.proposal_id}",
            proposal_id=proposal.proposal_id,
            approval_id=approval.approval_id,
            incident_id=context.incident_id,
            repository_owner=context.repository_owner,
            repository_name=context.repository_name,
            base_commit_sha=proposal.commit_sha,
            branch_name=f"akesis/fix/{context.incident_id}",
            status=MutationStatus.PR_CREATED,
            pr_number=501,
            pr_url=f"https://github.com/{context.repository_owner}/{context.repository_name}/pull/501",
        )
        async with self.session_factory() as session:
            repo = MutationRepository(session)
            return await repo.create_mutation(record)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool, echo=False)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def make_context(
    scenario_id: str,
    category: FailureCategory = FailureCategory.TEST,
    target_file: str = "src/calculator.py",
) -> FailureContext:
    now_ts = int(datetime.now(UTC).timestamp())
    return FailureContext(
        incident_id=f"inc_{scenario_id}_{now_ts}",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=9001,
        workflow_name="CI Test Suite",
        commit_sha="e0e1e2e3e4e5e6e7e8e9e0e1e2e3e4e5e6e7e8e9",
        branch="main",
        run_url="https://github.com/crlabs-ai/akesis/actions/runs/9001",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=category,
            error_type="AssertionError" if category == FailureCategory.TEST else "LintViolation",
            message=f"Benchmark failure in {target_file}",
            target_file=target_file,
            target_line=10,
        ),
        raw_log_excerpt=f"Error in {target_file} at line 10",
    )


# ==============================================================================
# Scenario A: Successful Ruff / Lint Remediation
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_a_successful_ruff_lint_remediation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = make_context("scen_a_ruff", category=FailureCategory.LINT, target_file="src/utils.py")
    lint_diff = (
        "--- a/src/utils.py\n+++ b/src/utils.py\n@@ -1,2 +1,1 @@\n"
        "-import unused_module\n import sys\n"
    )

    @asynccontextmanager
    async def scoped_repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        async with session_factory() as session:
            yield (
                ApprovalRepository(session),
                MutationRepository(session),
                PipelineRepository(session),
            )

    mutation_svc = BenchmarkMutationService(session_factory)
    orch = RemediationOrchestrator(
        repository_factory=scoped_repo_factory,
        diagnostic_service=BenchmarkDiagService(
            category=FailureCategory.LINT,
            target_file="src/utils.py",
            root_cause="Unused import F401",
        ),
        context_resolver=BenchmarkContextResolver(target_file="src/utils.py"),
        fix_service=BenchmarkFixService(target_file="src/utils.py", diff=lint_diff),
        validation_service=BenchmarkValidationService(status=ValidationStatus.PASSED, exit_code=0),
        approval_service=BenchmarkApprovalService(session_factory),
        mutation_service=mutation_svc,
    )

    # 1. Failure ingestion through approval request
    pipe = await orch.process_failure(context)
    assert pipe.status == PipelineStatus.AWAITING_APPROVAL
    assert pipe.proposal_json is not None

    # 2. Human approval in PostgreSQL
    assert pipe.approval_id is not None
    async with session_factory() as session:
        a_repo = ApprovalRepository(session)
        await a_repo.record_decision(
            pipe.approval_id, ApprovalStatus.APPROVED, reviewer="reviewer_a"
        )

    # 3. Resume and complete mutation
    final_pipe = await orch.resume_approval(pipe.approval_id)
    assert final_pipe.status == PipelineStatus.COMPLETED
    assert final_pipe.pr_number == 501
    assert mutation_svc.mutation_invoked
    assert mutation_svc.last_proposal is not None
    assert "-import unused_module" in mutation_svc.last_proposal.unified_diff


# ==============================================================================
# Scenario B: Successful Pytest Remediation
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_b_successful_pytest_remediation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = make_context(
        "scen_b_pytest", category=FailureCategory.TEST, target_file="src/calc.py"
    )
    test_diff = (
        "--- a/src/calc.py\n+++ b/src/calc.py\n@@ -5,2 +5,2 @@\n"
        "-    return a / 0\n+    return a / b if b != 0 else 0\n"
    )

    @asynccontextmanager
    async def scoped_repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        async with session_factory() as session:
            yield (
                ApprovalRepository(session),
                MutationRepository(session),
                PipelineRepository(session),
            )

    mutation_svc = BenchmarkMutationService(session_factory)
    orch = RemediationOrchestrator(
        repository_factory=scoped_repo_factory,
        diagnostic_service=BenchmarkDiagService(
            category=FailureCategory.TEST,
            target_file="src/calc.py",
            root_cause="ZeroDivisionError in calculate",
        ),
        context_resolver=BenchmarkContextResolver(target_file="src/calc.py"),
        fix_service=BenchmarkFixService(target_file="src/calc.py", diff=test_diff),
        validation_service=BenchmarkValidationService(status=ValidationStatus.PASSED, exit_code=0),
        approval_service=BenchmarkApprovalService(session_factory),
        mutation_service=mutation_svc,
    )

    pipe = await orch.process_failure(context)
    assert pipe.status == PipelineStatus.AWAITING_APPROVAL

    assert pipe.approval_id is not None
    async with session_factory() as session:
        a_repo = ApprovalRepository(session)
        await a_repo.record_decision(
            pipe.approval_id, ApprovalStatus.APPROVED, reviewer="reviewer_b"
        )

    final_pipe = await orch.resume_approval(pipe.approval_id)
    assert final_pipe.status == PipelineStatus.COMPLETED
    assert final_pipe.pr_number == 501
    assert mutation_svc.mutation_invoked


# ==============================================================================
# Scenario C: Successful Dependency / Config Remediation
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_c_successful_dependency_config_remediation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = make_context(
        "scen_c_dep", category=FailureCategory.DEPENDENCY, target_file="pyproject.toml"
    )
    dep_diff = (
        "--- a/pyproject.toml\n+++ b/pyproject.toml\n@@ -10,2 +10,3 @@\n"
        ' dependencies = [\n+    "pydantic>=2.0",\n ]\n'
    )

    @asynccontextmanager
    async def scoped_repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        async with session_factory() as session:
            yield (
                ApprovalRepository(session),
                MutationRepository(session),
                PipelineRepository(session),
            )

    mutation_svc = BenchmarkMutationService(session_factory)
    orch = RemediationOrchestrator(
        repository_factory=scoped_repo_factory,
        diagnostic_service=BenchmarkDiagService(
            category=FailureCategory.DEPENDENCY,
            target_file="pyproject.toml",
            root_cause="Missing dependency pydantic",
        ),
        context_resolver=BenchmarkContextResolver(target_file="pyproject.toml"),
        fix_service=BenchmarkFixService(target_file="pyproject.toml", diff=dep_diff),
        validation_service=BenchmarkValidationService(status=ValidationStatus.PASSED, exit_code=0),
        approval_service=BenchmarkApprovalService(session_factory),
        mutation_service=mutation_svc,
    )

    pipe = await orch.process_failure(context)
    assert pipe.status == PipelineStatus.AWAITING_APPROVAL

    assert pipe.approval_id is not None
    async with session_factory() as session:
        a_repo = ApprovalRepository(session)
        await a_repo.record_decision(
            pipe.approval_id, ApprovalStatus.APPROVED, reviewer="reviewer_c"
        )

    final_pipe = await orch.resume_approval(pipe.approval_id)
    assert final_pipe.status == PipelineStatus.COMPLETED


# ==============================================================================
# Scenario D: Low-Confidence Diagnosis Rejected / Halted
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_d_low_confidence_diagnosis_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = make_context("scen_d_low_conf")

    @asynccontextmanager
    async def scoped_repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        async with session_factory() as session:
            yield (
                ApprovalRepository(session),
                MutationRepository(session),
                PipelineRepository(session),
            )

    mutation_svc = BenchmarkMutationService(session_factory)
    orch = RemediationOrchestrator(
        repository_factory=scoped_repo_factory,
        diagnostic_service=BenchmarkDiagService(
            is_fixable=False,
            confidence_score=0.30,
            root_cause="Ambiguous third-party network outage",
        ),
        context_resolver=BenchmarkContextResolver(),
        fix_service=BenchmarkFixService(),
        validation_service=BenchmarkValidationService(),
        approval_service=BenchmarkApprovalService(session_factory),
        mutation_service=mutation_svc,
    )

    pipe = await orch.process_failure(context)
    assert pipe.status == PipelineStatus.FAILED
    assert "not fixable" in (pipe.failure_reason or "")
    assert not mutation_svc.mutation_invoked


# ==============================================================================
# Scenario E: Invalid / Malformed LLM Fix Proposal Rejected
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_e_malformed_fix_proposal_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = make_context("scen_e_malformed")

    @asynccontextmanager
    async def scoped_repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        async with session_factory() as session:
            yield (
                ApprovalRepository(session),
                MutationRepository(session),
                PipelineRepository(session),
            )

    mutation_svc = BenchmarkMutationService(session_factory)
    orch = RemediationOrchestrator(
        repository_factory=scoped_repo_factory,
        diagnostic_service=BenchmarkDiagService(),
        context_resolver=BenchmarkContextResolver(),
        fix_service=BenchmarkFixService(
            is_valid=False,
            status="rejected",
            rejection_reasons=["Malformed unified diff header: missing @@ line markers"],
        ),
        validation_service=BenchmarkValidationService(),
        approval_service=BenchmarkApprovalService(session_factory),
        mutation_service=mutation_svc,
    )

    pipe = await orch.process_failure(context)
    assert pipe.status == PipelineStatus.REJECTED
    assert "Malformed unified diff" in (pipe.failure_reason or "")
    assert not mutation_svc.mutation_invoked


# ==============================================================================
# Scenario F: Unsafe / Protected Patch Target Rejected
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_f_protected_patch_target_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = make_context("scen_f_protected", target_file=".github/workflows/ci.yml")

    @asynccontextmanager
    async def scoped_repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        async with session_factory() as session:
            yield (
                ApprovalRepository(session),
                MutationRepository(session),
                PipelineRepository(session),
            )

    mutation_svc = BenchmarkMutationService(session_factory)
    orch = RemediationOrchestrator(
        repository_factory=scoped_repo_factory,
        diagnostic_service=BenchmarkDiagService(target_file=".github/workflows/ci.yml"),
        context_resolver=BenchmarkContextResolver(target_file=".github/workflows/ci.yml"),
        fix_service=BenchmarkFixService(
            is_valid=False,
            status="rejected",
            target_file=".github/workflows/ci.yml",
            rejection_reasons=["Protected CI workflow path cannot be modified by AI patch"],
        ),
        validation_service=BenchmarkValidationService(),
        approval_service=BenchmarkApprovalService(session_factory),
        mutation_service=mutation_svc,
    )

    pipe = await orch.process_failure(context)
    assert pipe.status == PipelineStatus.REJECTED
    assert "Protected CI workflow" in (pipe.failure_reason or "")
    assert not mutation_svc.mutation_invoked


# ==============================================================================
# Scenario G: Sandbox Validation Failure Prevents Mutation
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_g_sandbox_validation_failure_halts_pipeline(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = make_context("scen_g_val_fail")

    @asynccontextmanager
    async def scoped_repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        async with session_factory() as session:
            yield (
                ApprovalRepository(session),
                MutationRepository(session),
                PipelineRepository(session),
            )

    mutation_svc = BenchmarkMutationService(session_factory)
    orch = RemediationOrchestrator(
        repository_factory=scoped_repo_factory,
        diagnostic_service=BenchmarkDiagService(),
        context_resolver=BenchmarkContextResolver(),
        fix_service=BenchmarkFixService(),
        validation_service=BenchmarkValidationService(
            status=ValidationStatus.FAILED,
            exit_code=1,
            stderr="FAILED tests/test_calc.py::test_calc - AssertionError",
        ),
        approval_service=BenchmarkApprovalService(session_factory),
        mutation_service=mutation_svc,
    )

    pipe = await orch.process_failure(context)
    assert pipe.status == PipelineStatus.FAILED
    assert "Sandbox validation failed" in (pipe.failure_reason or "")
    assert not mutation_svc.mutation_invoked


# ==============================================================================
# Scenario H: Human Approval Rejection Prevents Mutation
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_h_human_approval_rejection_prevents_mutation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = make_context("scen_h_reject")

    @asynccontextmanager
    async def scoped_repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        async with session_factory() as session:
            yield (
                ApprovalRepository(session),
                MutationRepository(session),
                PipelineRepository(session),
            )

    mutation_svc = BenchmarkMutationService(session_factory)
    orch = RemediationOrchestrator(
        repository_factory=scoped_repo_factory,
        diagnostic_service=BenchmarkDiagService(),
        context_resolver=BenchmarkContextResolver(),
        fix_service=BenchmarkFixService(),
        validation_service=BenchmarkValidationService(),
        approval_service=BenchmarkApprovalService(session_factory),
        mutation_service=mutation_svc,
    )

    pipe = await orch.process_failure(context)
    assert pipe.status == PipelineStatus.AWAITING_APPROVAL

    assert pipe.approval_id is not None
    async with session_factory() as session:
        a_repo = ApprovalRepository(session)
        await a_repo.record_decision(
            pipe.approval_id, ApprovalStatus.REJECTED, reviewer="security_lead"
        )

    final_pipe = await orch.resume_approval(pipe.approval_id)
    assert final_pipe.status == PipelineStatus.REJECTED
    assert not mutation_svc.mutation_invoked


# ==============================================================================
# Scenario I: Approval Expiry Prevents Mutation
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_i_approval_expiry_prevents_mutation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = make_context("scen_i_expiry")

    @asynccontextmanager
    async def scoped_repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        async with session_factory() as session:
            yield (
                ApprovalRepository(session),
                MutationRepository(session),
                PipelineRepository(session),
            )

    mutation_svc = BenchmarkMutationService(session_factory)
    orch = RemediationOrchestrator(
        repository_factory=scoped_repo_factory,
        diagnostic_service=BenchmarkDiagService(),
        context_resolver=BenchmarkContextResolver(),
        fix_service=BenchmarkFixService(),
        validation_service=BenchmarkValidationService(),
        approval_service=BenchmarkApprovalService(session_factory),
        mutation_service=mutation_svc,
    )

    pipe = await orch.process_failure(context)
    assert pipe.status == PipelineStatus.AWAITING_APPROVAL

    assert pipe.approval_id is not None
    async with session_factory() as session:
        a_repo = ApprovalRepository(session)
        await a_repo.expire_approval(pipe.approval_id)

    with pytest.raises(OrchestrationError, match="must be 'approved'"):
        await orch.resume_approval(pipe.approval_id)

    assert not mutation_svc.mutation_invoked


# ==============================================================================
# Scenario J: Stale Commit SHA Prevents Mutation
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_j_stale_commit_sha_prevents_mutation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = make_context("scen_j_stale_sha")

    @asynccontextmanager
    async def scoped_repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        async with session_factory() as session:
            yield (
                ApprovalRepository(session),
                MutationRepository(session),
                PipelineRepository(session),
            )

    mutation_svc = BenchmarkMutationService(session_factory, stale_sha=True)
    orch = RemediationOrchestrator(
        repository_factory=scoped_repo_factory,
        diagnostic_service=BenchmarkDiagService(),
        context_resolver=BenchmarkContextResolver(),
        fix_service=BenchmarkFixService(),
        validation_service=BenchmarkValidationService(),
        approval_service=BenchmarkApprovalService(session_factory),
        mutation_service=mutation_svc,
    )

    pipe = await orch.process_failure(context)
    assert pipe.approval_id is not None

    async with session_factory() as session:
        a_repo = ApprovalRepository(session)
        await a_repo.record_decision(pipe.approval_id, ApprovalStatus.APPROVED, reviewer="alice")

    final_pipe = await orch.resume_approval(pipe.approval_id)
    assert final_pipe.status == PipelineStatus.FAILED
    assert "does not match proposal commit sha" in (final_pipe.failure_reason or "")


# ==============================================================================
# Scenario K: Duplicate Remediation Request is Idempotent
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_k_duplicate_request_idempotency(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = make_context("scen_k_idempotency")

    @asynccontextmanager
    async def scoped_repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        async with session_factory() as session:
            yield (
                ApprovalRepository(session),
                MutationRepository(session),
                PipelineRepository(session),
            )

    orch = RemediationOrchestrator(
        repository_factory=scoped_repo_factory,
        diagnostic_service=BenchmarkDiagService(),
        context_resolver=BenchmarkContextResolver(),
        fix_service=BenchmarkFixService(),
        validation_service=BenchmarkValidationService(),
        approval_service=BenchmarkApprovalService(session_factory),
        mutation_service=BenchmarkMutationService(session_factory),
    )

    # First delivery
    pipe1 = await orch.process_failure(context)
    # Duplicate webhook delivery
    pipe2 = await orch.process_failure(context)

    assert pipe1.pipeline_id == pipe2.pipeline_id
    assert pipe1.status == pipe2.status


# ==============================================================================
# Scenario L: Successful Approved Mutation Reaches GitHub PR Delivery
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_l_end_to_end_delivery_through_mock_boundary(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = make_context("scen_l_e2e_pr")

    @asynccontextmanager
    async def scoped_repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        async with session_factory() as session:
            yield (
                ApprovalRepository(session),
                MutationRepository(session),
                PipelineRepository(session),
            )

    mutation_svc = BenchmarkMutationService(session_factory)
    orch = RemediationOrchestrator(
        repository_factory=scoped_repo_factory,
        diagnostic_service=BenchmarkDiagService(),
        context_resolver=BenchmarkContextResolver(),
        fix_service=BenchmarkFixService(),
        validation_service=BenchmarkValidationService(),
        approval_service=BenchmarkApprovalService(session_factory),
        mutation_service=mutation_svc,
    )

    pipe = await orch.process_failure(context)
    assert pipe.approval_id is not None

    # Human approves
    async with session_factory() as session:
        a_repo = ApprovalRepository(session)
        await a_repo.record_decision(pipe.approval_id, ApprovalStatus.APPROVED, reviewer="lead_dev")

    final_pipe = await orch.resume_approval(pipe.approval_id)
    assert final_pipe.status == PipelineStatus.COMPLETED
    assert final_pipe.pr_number == 501
    assert final_pipe.pr_url == "https://github.com/crlabs-ai/akesis/pull/501"
    assert mutation_svc.mutation_invoked
