from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from src.packages.database.repositories import (
    ApprovalRepositoryProtocol,
    MutationRepositoryProtocol,
    PipelineRepositoryProtocol,
)
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
    PipelineRecord,
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


class MockApprovalRepo(ApprovalRepositoryProtocol):
    def __init__(self) -> None:
        self.records: dict[str, ApprovalRecord] = {}

    async def create_approval(self, record: ApprovalRecord) -> ApprovalRecord:
        self.records[record.approval_id] = record
        return record

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        return self.records.get(approval_id)

    async def get_by_proposal_id(self, proposal_id: str) -> ApprovalRecord | None:
        for r in self.records.values():
            if r.proposal_id == proposal_id:
                return r
        return None

    async def record_decision(
        self,
        approval_id: str,
        target_status: ApprovalStatus,
        reviewer: str,
        reason: str | None = None,
    ) -> tuple[ApprovalRecord | None, bool]:
        rec = self.records.get(approval_id)
        if rec and rec.status == ApprovalStatus.PENDING:
            rec.status = target_status
            rec.decided_by = reviewer
            rec.decision_reason = reason
            rec.decided_at = datetime.now(UTC)
            return rec, False
        return rec, True

    async def expire_approval(self, approval_id: str) -> bool:
        return True


class MockMutationRepo(MutationRepositoryProtocol):
    def __init__(self) -> None:
        self.records: dict[str, MutationRecord] = {}

    async def create_mutation(self, record: MutationRecord) -> MutationRecord:
        self.records[record.mutation_id] = record
        return record

    async def get_mutation(self, mutation_id: str) -> MutationRecord | None:
        return self.records.get(mutation_id)

    async def get_by_proposal_and_commit(
        self, proposal_id: str, commit_sha: str
    ) -> MutationRecord | None:
        for r in self.records.values():
            if r.proposal_id == proposal_id and r.base_commit_sha == commit_sha:
                return r
        return None

    async def update_status(
        self,
        mutation_id: str,
        status: MutationStatus,
        resulting_commit_sha: str | None = None,
        validation_status: ValidationStatus | None = None,
        pr_number: int | None = None,
        pr_url: str | None = None,
        failure_reason: str | None = None,
    ) -> MutationRecord | None:
        rec = self.records.get(mutation_id)
        if rec:
            rec.status = status
            if resulting_commit_sha is not None:
                rec.resulting_commit_sha = resulting_commit_sha
            if pr_number is not None:
                rec.pr_number = pr_number
            if pr_url is not None:
                rec.pr_url = pr_url
            if failure_reason is not None:
                rec.failure_reason = failure_reason
        return rec


class MockPipelineRepo(PipelineRepositoryProtocol):
    def __init__(self) -> None:
        self.records: dict[str, PipelineRecord] = {}

    async def create_pipeline(self, record: PipelineRecord) -> PipelineRecord:
        self.records[record.pipeline_id] = record
        return record

    async def get_pipeline(self, pipeline_id: str) -> PipelineRecord | None:
        return self.records.get(pipeline_id)

    async def get_by_incident_id(self, incident_id: str) -> PipelineRecord | None:
        for r in self.records.values():
            if r.incident_id == incident_id:
                return r
        return None

    async def get_by_approval_id(self, approval_id: str) -> PipelineRecord | None:
        for r in self.records.values():
            if r.approval_id == approval_id:
                return r
        return None

    async def update_pipeline_state(
        self,
        pipeline_id: str,
        status: PipelineStatus,
        diagnosis_id: str | None = None,
        proposal_id: str | None = None,
        approval_id: str | None = None,
        mutation_id: str | None = None,
        pr_number: int | None = None,
        pr_url: str | None = None,
        failure_reason: str | None = None,
        failure_context_json: str | None = None,
        proposal_json: str | None = None,
        validation_json: str | None = None,
    ) -> PipelineRecord | None:
        rec = self.records.get(pipeline_id)
        if rec:
            rec.status = status
            if diagnosis_id is not None:
                rec.diagnosis_id = diagnosis_id
            if proposal_id is not None:
                rec.proposal_id = proposal_id
            if approval_id is not None:
                rec.approval_id = approval_id
            if mutation_id is not None:
                rec.mutation_id = mutation_id
            if pr_number is not None:
                rec.pr_number = pr_number
            if pr_url is not None:
                rec.pr_url = pr_url
            if failure_reason is not None:
                rec.failure_reason = failure_reason
            if failure_context_json is not None:
                rec.failure_context_json = failure_context_json
            if proposal_json is not None:
                rec.proposal_json = proposal_json
            if validation_json is not None:
                rec.validation_json = validation_json
            rec.updated_at = datetime.now(UTC)
        return rec


class MockDiagService:
    def __init__(self, is_fixable: bool = True) -> None:
        self.is_fixable = is_fixable

    async def diagnose_failure(
        self, context: FailureContext, evidence_package: EvidencePackage | None = None
    ) -> DiagnosticResult:
        proposal = DiagnosisProposal(
            category=FailureCategory.TEST,
            root_cause="Assertion failed",
            evidence=[EvidenceItem(source="log", observation="assert 1 == 2")],
            target_file="test_app.py",
            remediation_direction=RemediationDirection(
                summary="Fix assertion", suggested_action="Update expected", risk_assessment="None"
            ),
            is_fixable=self.is_fixable,
            confidence_score=0.95,
            evidence_sufficiency="sufficient",
            reasoning="Simple fix",
        )
        return DiagnosticResult(
            incident_id=context.incident_id,
            proposal=proposal,
            model_name="gemini-2.5-pro",
            execution_time_ms=50.0,
        )


class MockContextResolver:
    def resolve_context(
        self, failure_context: FailureContext, repo_root: Any = None
    ) -> EvidencePackage:
        return EvidencePackage(
            incident_id=failure_context.incident_id,
            commit_sha=failure_context.commit_sha,
            failure_context=failure_context,
            code_evidences=[
                CodeEvidence(
                    path="test_app.py",
                    start_line=1,
                    end_line=10,
                    content="def test_foo(): assert 1 == 2\n",
                    total_file_lines=10,
                )
            ],
            retrieval_status="success",
        )


class MockFixService:
    def __init__(self, is_valid: bool = True) -> None:
        self.is_valid = is_valid

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
            status="proposed" if self.is_valid else "rejected",
            is_valid=self.is_valid,
            rejection_reasons=[] if self.is_valid else ["Unsafe patch limits exceeded"],
            unified_diff=(
                "--- a/test_app.py\n+++ b/test_app.py\n@@ -1,1 +1,1 @@\n"
                "-assert 1 == 2\n+assert 1 == 1\n"
            ),
            target_files=["test_app.py"],
            rationale="Fix assertion",
            risk_level="low",
            confidence_score=0.95,
        )


class MockValidationService:
    def __init__(
        self, status: ValidationStatus = ValidationStatus.PASSED, exit_code: int = 0
    ) -> None:
        self.status = status
        self.exit_code = exit_code

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
            duration_ms=10.0,
        )


class MockApprovalService:
    def __init__(self, appr_repo: MockApprovalRepo) -> None:
        self.appr_repo = appr_repo

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
        return await self.appr_repo.create_approval(record)


class MockMutationService:
    def __init__(
        self,
        mut_repo: MockMutationRepo,
        should_fail: bool = False,
        stale_sha: bool = False,
    ) -> None:
        self.mut_repo = mut_repo
        self.should_fail = should_fail
        self.stale_sha = stale_sha
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

            raise MutationError("Git push rejected")
        record = MutationRecord(
            mutation_id=f"mut_{proposal.proposal_id}",
            proposal_id=proposal.proposal_id,
            approval_id=approval.approval_id,
            incident_id=context.incident_id,
            repository_owner=context.repository_owner,
            repository_name=context.repository_name,
            base_commit_sha=proposal.commit_sha,
            branch_name="akesis/fix/1",
            status=MutationStatus.PR_CREATED,
            pr_number=123,
            pr_url="https://github.com/crlabs-ai/akesis/pull/123",
        )
        return await self.mut_repo.create_mutation(record)


@pytest.fixture
def sample_context() -> FailureContext:
    return FailureContext(
        incident_id="inc_orch_01",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=555,
        workflow_name="CI Test",
        commit_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        branch="main",
        run_url="https://github.com/crlabs-ai/akesis/actions/runs/555",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=FailureCategory.TEST, error_type="AssertionError", target_file="test_app.py"
        ),
        raw_log_excerpt="assert 1 == 2",
    )


# 1. Successful pipeline through approval-pending state.
@pytest.mark.asyncio
async def test_orchestrator_success_to_awaiting_approval(sample_context: FailureContext) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    mut_svc = MockMutationService(mut_repo)
    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(),
        context_resolver=MockContextResolver(),
        fix_service=MockFixService(),
        validation_service=MockValidationService(),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=mut_svc,
    )

    record = await orch.process_failure(sample_context)
    assert record.status == PipelineStatus.AWAITING_APPROVAL
    assert record.approval_id is not None
    assert record.proposal_id is not None
    assert record.proposal_json is not None
    assert record.validation_json is not None
    # Verify NO mutation occurred before approval
    assert not mut_svc.mutation_invoked


# 2. Diagnosis failure stops pipeline.
@pytest.mark.asyncio
async def test_orchestrator_halts_on_unfixable_diagnosis(sample_context: FailureContext) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(is_fixable=False),
        context_resolver=MockContextResolver(),
        fix_service=MockFixService(),
        validation_service=MockValidationService(),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=MockMutationService(mut_repo),
    )

    record = await orch.process_failure(sample_context)
    assert record.status == PipelineStatus.FAILED
    assert "not fixable" in (record.failure_reason or "")


# 3. Ineligible fix proposal stops pipeline.
@pytest.mark.asyncio
async def test_orchestrator_halts_on_invalid_fix_proposal(sample_context: FailureContext) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(),
        context_resolver=MockContextResolver(),
        fix_service=MockFixService(is_valid=False),
        validation_service=MockValidationService(),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=MockMutationService(mut_repo),
    )

    record = await orch.process_failure(sample_context)
    assert record.status == PipelineStatus.REJECTED


# 4. Validation failure stops pipeline.
@pytest.mark.asyncio
async def test_orchestrator_halts_on_validation_failure(sample_context: FailureContext) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(),
        context_resolver=MockContextResolver(),
        fix_service=MockFixService(),
        validation_service=MockValidationService(status=ValidationStatus.FAILED, exit_code=1),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=MockMutationService(mut_repo),
    )

    record = await orch.process_failure(sample_context)
    assert record.status == PipelineStatus.FAILED


# 5. Approval request creates awaiting_approval state.
@pytest.mark.asyncio
async def test_orchestrator_creates_awaiting_approval_state(
    sample_context: FailureContext,
) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(),
        context_resolver=MockContextResolver(),
        fix_service=MockFixService(),
        validation_service=MockValidationService(),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=MockMutationService(mut_repo),
    )

    record = await orch.process_failure(sample_context)
    assert record.status == PipelineStatus.AWAITING_APPROVAL
    assert record.approval_id == f"appr_prop_{sample_context.incident_id}"


# 6. Rejected approval stops pipeline.
@pytest.mark.asyncio
async def test_orchestrator_resume_on_rejected_decision(sample_context: FailureContext) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    mut_svc = MockMutationService(mut_repo)
    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(),
        context_resolver=MockContextResolver(),
        fix_service=MockFixService(),
        validation_service=MockValidationService(),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=mut_svc,
    )

    pipe_rec = await orch.process_failure(sample_context)
    approval_id = pipe_rec.approval_id
    assert approval_id is not None

    # Human rejects
    await appr_repo.record_decision(approval_id, ApprovalStatus.REJECTED, reviewer="bob")

    final_rec = await orch.resume_approval(approval_id=approval_id)
    assert final_rec.status == PipelineStatus.REJECTED
    # Mutation must never be called on rejection
    assert not mut_svc.mutation_invoked


# 7. Approved approval resumes pipeline with EXACT persisted patch.
@pytest.mark.asyncio
async def test_orchestrator_resume_uses_exact_persisted_patch(
    sample_context: FailureContext,
) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    mut_svc = MockMutationService(mut_repo)
    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(),
        context_resolver=MockContextResolver(),
        fix_service=MockFixService(),
        validation_service=MockValidationService(),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=mut_svc,
    )

    # 1. Start pipeline to awaiting_approval
    pipe_rec = await orch.process_failure(sample_context)
    assert pipe_rec.status == PipelineStatus.AWAITING_APPROVAL
    approval_id = pipe_rec.approval_id
    assert approval_id is not None

    # 2. Simulate human decision in DB
    await appr_repo.record_decision(approval_id, ApprovalStatus.APPROVED, reviewer="alice")

    # 3. Resume pipeline
    final_rec = await orch.resume_approval(approval_id=approval_id)
    assert final_rec.status == PipelineStatus.COMPLETED
    assert final_rec.pr_number == 123
    assert mut_svc.mutation_invoked
    assert mut_svc.last_proposal is not None
    assert "assert 1 == 1" in mut_svc.last_proposal.unified_diff


# 8. Resume cannot fabricate proposal if durable record missing.
@pytest.mark.asyncio
async def test_orchestrator_resume_fails_if_proposal_json_missing(
    sample_context: FailureContext,
) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(),
        context_resolver=MockContextResolver(),
        fix_service=MockFixService(),
        validation_service=MockValidationService(),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=MockMutationService(mut_repo),
    )

    pipe_rec = await orch.process_failure(sample_context)
    approval_id = pipe_rec.approval_id
    assert approval_id is not None
    await appr_repo.record_decision(approval_id, ApprovalStatus.APPROVED, reviewer="alice")

    # Corrupt pipeline by removing proposal_json
    pipe = pipe_repo.records[pipe_rec.pipeline_id]
    pipe.proposal_json = None

    with pytest.raises(OrchestrationError, match="fix proposal missing"):
        await orch.resume_approval(approval_id=approval_id)


# 9. Stale commit SHA prevents mutation.
@pytest.mark.asyncio
async def test_orchestrator_stale_commit_sha_prevents_mutation(
    sample_context: FailureContext,
) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(),
        context_resolver=MockContextResolver(),
        fix_service=MockFixService(),
        validation_service=MockValidationService(),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=MockMutationService(mut_repo, stale_sha=True),
    )

    pipe_rec = await orch.process_failure(sample_context)
    approval_id = pipe_rec.approval_id
    assert approval_id is not None
    await appr_repo.record_decision(approval_id, ApprovalStatus.APPROVED, reviewer="alice")

    final_rec = await orch.resume_approval(approval_id=approval_id)
    assert final_rec.status == PipelineStatus.FAILED
    assert "does not match proposal commit sha" in (final_rec.failure_reason or "")


# 10. Mismatched approval proposal ID is rejected.
@pytest.mark.asyncio
async def test_orchestrator_mismatched_proposal_id_rejected(
    sample_context: FailureContext,
) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(),
        context_resolver=MockContextResolver(),
        fix_service=MockFixService(),
        validation_service=MockValidationService(),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=MockMutationService(mut_repo),
    )

    pipe_rec = await orch.process_failure(sample_context)
    approval_id = pipe_rec.approval_id
    assert approval_id is not None
    await appr_repo.record_decision(approval_id, ApprovalStatus.APPROVED, reviewer="alice")

    # Mismatch approval proposal ID
    appr = appr_repo.records[approval_id]
    appr.proposal_id = "prop_tampered_diff"

    with pytest.raises(OrchestrationError, match="does not match"):
        await orch.resume_approval(approval_id=approval_id)


# 11. Duplicate webhook is idempotent.
@pytest.mark.asyncio
async def test_orchestrator_duplicate_incident_is_idempotent(
    sample_context: FailureContext,
) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(),
        context_resolver=MockContextResolver(),
        fix_service=MockFixService(),
        validation_service=MockValidationService(),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=MockMutationService(mut_repo),
    )

    rec1 = await orch.process_failure(sample_context)
    rec2 = await orch.process_failure(sample_context)
    assert rec1.pipeline_id == rec2.pipeline_id


# 12. Duplicate approval/resume is idempotent.
@pytest.mark.asyncio
async def test_orchestrator_duplicate_resume_is_idempotent(
    sample_context: FailureContext,
) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(),
        context_resolver=MockContextResolver(),
        fix_service=MockFixService(),
        validation_service=MockValidationService(),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=MockMutationService(mut_repo),
    )

    pipe_rec = await orch.process_failure(sample_context)
    approval_id = pipe_rec.approval_id
    assert approval_id is not None
    await appr_repo.record_decision(approval_id, ApprovalStatus.APPROVED, reviewer="alice")

    final1 = await orch.resume_approval(approval_id=approval_id)
    assert final1.status == PipelineStatus.COMPLETED

    # Repeat resume
    final2 = await orch.resume_approval(approval_id=approval_id)
    assert final2.status == PipelineStatus.COMPLETED
    assert final2.pipeline_id == final1.pipeline_id


# 13. Mutation failure produces failed state.
@pytest.mark.asyncio
async def test_orchestrator_mutation_failure_produces_failed_state(
    sample_context: FailureContext,
) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(),
        context_resolver=MockContextResolver(),
        fix_service=MockFixService(),
        validation_service=MockValidationService(),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=MockMutationService(mut_repo, should_fail=True),
    )

    pipe_rec = await orch.process_failure(sample_context)
    approval_id = pipe_rec.approval_id
    assert approval_id is not None
    await appr_repo.record_decision(approval_id, ApprovalStatus.APPROVED, reviewer="alice")

    final_rec = await orch.resume_approval(approval_id=approval_id)
    assert final_rec.status == PipelineStatus.FAILED
    assert "Git push rejected" in (final_rec.failure_reason or "")


# 14. Expired approval halts pipeline resumption.
@pytest.mark.asyncio
async def test_orchestrator_expired_approval_rejected(
    sample_context: FailureContext,
) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(),
        context_resolver=MockContextResolver(),
        fix_service=MockFixService(),
        validation_service=MockValidationService(),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=MockMutationService(mut_repo),
    )

    pipe_rec = await orch.process_failure(sample_context)
    approval_id = pipe_rec.approval_id
    assert approval_id is not None
    await appr_repo.expire_approval(approval_id)

    with pytest.raises(OrchestrationError, match="must be 'approved'"):
        await orch.resume_approval(approval_id=approval_id)


# 15. Protected target path causes proposal rejection.
@pytest.mark.asyncio
async def test_orchestrator_protected_target_rejection(
    sample_context: FailureContext,
) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    class ProtectedFixService:
        async def generate_fix_proposal(self, *args: Any, **kwargs: Any) -> FixProposal:
            return FixProposal(
                proposal_id="prop_protected",
                incident_id="inc_test_123",
                diagnosis_id="diag_123",
                commit_sha=sample_context.commit_sha,
                status="rejected",
                is_valid=False,
                rejection_reasons=[
                    "Target path '.github/workflows/ci.yml' matches protected security pattern."
                ],
                unified_diff="",
                file_patches=[],
                target_files=[".github/workflows/ci.yml"],
                rationale="cannot modify workflow",
                assumptions=[],
                risk_level="high",
                has_dependency_changes=False,
                confidence_score=0.0,
                created_at=datetime.now(UTC),
            )

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(),
        context_resolver=MockContextResolver(),
        fix_service=ProtectedFixService(),
        validation_service=MockValidationService(),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=MockMutationService(mut_repo),
    )

    pipe_rec = await orch.process_failure(sample_context)
    assert pipe_rec.status == PipelineStatus.REJECTED
    assert "matches protected security pattern" in (pipe_rec.failure_reason or "")


# 16. Context unavailable halts pipeline and prevents fix synthesis.
@pytest.mark.asyncio
async def test_orchestrator_halts_when_codebase_context_unavailable(
    sample_context: FailureContext,
) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    class UnavailableContextResolver:
        def resolve_context(self, *args: Any, **kwargs: Any) -> EvidencePackage:
            return EvidencePackage(
                incident_id=sample_context.incident_id,
                commit_sha=sample_context.commit_sha,
                failure_context=sample_context,
                code_evidences=[],
                retrieval_status="unavailable",
                retrieval_notes=["Repository checkout failed: GitCommandError"],
            )

    class SpyFixService:
        def __init__(self) -> None:
            self.called = False

        async def generate_fix_proposal(self, *args: Any, **kwargs: Any) -> FixProposal:
            self.called = True
            raise AssertionError("Fix service must NEVER be called when context is unavailable!")

    spy_fix = SpyFixService()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(),
        context_resolver=UnavailableContextResolver(),
        fix_service=spy_fix,
        validation_service=MockValidationService(),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=MockMutationService(mut_repo),
    )

    pipe_rec = await orch.process_failure(sample_context)
    assert pipe_rec.status == PipelineStatus.FAILED
    assert "Codebase context unavailable" in (pipe_rec.failure_reason or "")
    assert spy_fix.called is False
    # Verify no approval or mutation was created
    assert pipe_rec.approval_id is None
    assert pipe_rec.mutation_id is None


# 17. Zero relevant source snippets halts pipeline and prevents fix synthesis.
@pytest.mark.asyncio
async def test_orchestrator_halts_when_zero_relevant_source_snippets(
    sample_context: FailureContext,
) -> None:
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()
    pipe_repo = MockPipelineRepo()

    class EmptyContextResolver:
        def resolve_context(self, *args: Any, **kwargs: Any) -> EvidencePackage:
            return EvidencePackage(
                incident_id=sample_context.incident_id,
                commit_sha=sample_context.commit_sha,
                failure_context=sample_context,
                code_evidences=[],
                retrieval_status="empty",
                retrieval_notes=["No target source file identified"],
            )

    class SpyFixService:
        def __init__(self) -> None:
            self.called = False

        async def generate_fix_proposal(self, *args: Any, **kwargs: Any) -> FixProposal:
            self.called = True
            raise AssertionError("Fix service must NEVER be called when source snippets are empty!")

    spy_fix = SpyFixService()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[
        tuple[
            ApprovalRepositoryProtocol,
            MutationRepositoryProtocol,
            PipelineRepositoryProtocol,
        ]
    ]:
        yield (appr_repo, mut_repo, pipe_repo)

    orch = RemediationOrchestrator(
        repository_factory=repo_factory,
        diagnostic_service=MockDiagService(),
        context_resolver=EmptyContextResolver(),
        fix_service=spy_fix,
        validation_service=MockValidationService(),
        approval_service=MockApprovalService(appr_repo),
        mutation_service=MockMutationService(mut_repo),
    )

    pipe_rec = await orch.process_failure(sample_context)
    assert pipe_rec.status == PipelineStatus.FAILED
    assert "Codebase context unavailable" in (pipe_rec.failure_reason or "")
    assert spy_fix.called is False
