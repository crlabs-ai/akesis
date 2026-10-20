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
from src.packages.shared.remediation_orchestrator import RemediationOrchestrator


class MockIntDiagService:
    async def diagnose_failure(
        self, context: FailureContext, evidence_package: EvidencePackage | None = None
    ) -> DiagnosticResult:
        proposal = DiagnosisProposal(
            category=FailureCategory.TEST,
            root_cause="Integration assertion failed",
            evidence=[EvidenceItem(source="log", observation="assert True == False")],
            target_file="app.py",
            remediation_direction=RemediationDirection(
                summary="Fix", suggested_action="Fix assertion", risk_assessment="None"
            ),
            is_fixable=True,
            confidence_score=0.99,
            evidence_sufficiency="sufficient",
            reasoning="Reasoning",
        )
        return DiagnosticResult(
            incident_id=context.incident_id,
            proposal=proposal,
            model_name="gemini-2.5-pro",
            execution_time_ms=25.0,
        )


class MockIntContextResolver:
    def resolve_context(
        self, failure_context: FailureContext, repo_root: Any = None
    ) -> EvidencePackage:
        return EvidencePackage(
            incident_id=failure_context.incident_id,
            commit_sha=failure_context.commit_sha,
            failure_context=failure_context,
            code_evidences=[
                CodeEvidence(
                    path="app.py",
                    start_line=1,
                    end_line=5,
                    content="def test(): assert True\n",
                    total_file_lines=5,
                )
            ],
            retrieval_status="success",
        )


class MockIntFixService:
    async def generate_fix_proposal(
        self,
        context: FailureContext,
        evidence_package: EvidencePackage,
        diagnostic_result: DiagnosticResult | None = None,
    ) -> FixProposal:
        return FixProposal(
            proposal_id=f"prop_int_{context.incident_id}",
            incident_id=context.incident_id,
            commit_sha=context.commit_sha,
            status="proposed",
            is_valid=True,
            unified_diff=(
                "--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n"
                "-def test(): assert False\n+def test(): assert True\n"
            ),
            target_files=["app.py"],
            rationale="Fix bool",
            risk_level="low",
            confidence_score=0.99,
        )


class MockIntValidationService:
    async def validate_fix(
        self, proposal: FixProposal, context: FailureContext, repo_root: Any = None
    ) -> ValidationResult:
        return ValidationResult(
            validation_id=f"val_int_{proposal.proposal_id}",
            proposal_id=proposal.proposal_id,
            incident_id=context.incident_id,
            commit_sha=context.commit_sha,
            status=ValidationStatus.PASSED,
            command_executed="pytest",
            exit_code=0,
            duration_ms=10.0,
        )


class MockIntApprovalService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def request_approval(
        self, context: FailureContext, proposal: FixProposal, validation: ValidationResult
    ) -> ApprovalRecord:
        record = ApprovalRecord(
            approval_id=f"appr_int_{proposal.proposal_id}",
            incident_id=context.incident_id,
            proposal_id=proposal.proposal_id,
            commit_sha=context.commit_sha,
            status=ApprovalStatus.PENDING,
        )
        async with self.session_factory() as session:
            repo = ApprovalRepository(session)
            return await repo.create_approval(record)


class MockIntMutationService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create_pull_request(
        self,
        context: FailureContext,
        proposal: FixProposal,
        validation: ValidationResult,
        approval: ApprovalRecord,
    ) -> MutationRecord:
        record = MutationRecord(
            mutation_id=f"mut_int_{proposal.proposal_id}",
            proposal_id=proposal.proposal_id,
            approval_id=approval.approval_id,
            incident_id=context.incident_id,
            repository_owner=context.repository_owner,
            repository_name=context.repository_name,
            base_commit_sha=proposal.commit_sha,
            branch_name="akesis/fix/int",
            status=MutationStatus.PR_CREATED,
            pr_number=202,
            pr_url="https://github.com/crlabs-ai/akesis/pull/202",
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


@pytest.mark.asyncio
async def test_end_to_end_orchestrator_postgres_flow(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    uid = f"{now.timestamp():.6f}".replace(".", "_")

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
        diagnostic_service=MockIntDiagService(),
        context_resolver=MockIntContextResolver(),
        fix_service=MockIntFixService(),
        validation_service=MockIntValidationService(),
        approval_service=MockIntApprovalService(session_factory),
        mutation_service=MockIntMutationService(session_factory),
    )

    context = FailureContext(
        incident_id=f"inc_e2e_{uid}",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=888,
        workflow_name="CI",
        commit_sha="c0ffee0000000000000000000000000000000000",
        branch="main",
        run_url="https://ci",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=FailureCategory.TEST, error_type="AssertionError", target_file="app.py"
        ),
        raw_log_excerpt="assert True == False",
    )

    # 1. Process failure
    pipe_rec = await orch.process_failure(context)
    assert pipe_rec.status == PipelineStatus.AWAITING_APPROVAL

    # 2. Verify pipeline and approval persisted in PostgreSQL
    async with session_factory() as session:
        p_repo = PipelineRepository(session)
        a_repo = ApprovalRepository(session)
        p_in_db = await p_repo.get_pipeline(pipe_rec.pipeline_id)
        assert p_in_db is not None
        assert p_in_db.status == PipelineStatus.AWAITING_APPROVAL

        assert pipe_rec.approval_id is not None
        # 3. Simulate human approval decision in PostgreSQL
        appr, _ = await a_repo.record_decision(
            pipe_rec.approval_id, ApprovalStatus.APPROVED, reviewer="cholan"
        )
        assert appr is not None

    # 4. Resume orchestration
    assert pipe_rec.approval_id is not None
    final_rec = await orch.resume_approval(approval_id=pipe_rec.approval_id)
    assert final_rec.status == PipelineStatus.COMPLETED
    assert final_rec.pr_number == 202

    # 5. Verify final pipeline state in PostgreSQL
    async with session_factory() as session:
        p_repo = PipelineRepository(session)
        p_final = await p_repo.get_pipeline(pipe_rec.pipeline_id)
        assert p_final is not None
        assert p_final.status == PipelineStatus.COMPLETED
        assert p_final.pr_number == 202
