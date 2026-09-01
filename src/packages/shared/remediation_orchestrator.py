from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

from src.packages.database.repositories import (
    ApprovalRepository,
    ApprovalRepositoryProtocol,
    MutationRepository,
    MutationRepositoryProtocol,
    PipelineRepository,
    PipelineRepositoryProtocol,
)
from src.packages.database.session import get_session_factory
from src.packages.shared.approval_service import ApprovalService
from src.packages.shared.context_resolver import CodebaseContextResolver
from src.packages.shared.diagnostic_service import DiagnosticService
from src.packages.shared.fix_service import FixProposalService
from src.packages.shared.logging import get_logger
from src.packages.shared.models import (
    ApprovalRecord,
    ApprovalStatus,
    DiagnosticResult,
    EvidencePackage,
    FailureContext,
    FixProposal,
    PipelineRecord,
    PipelineStatus,
    ValidationResult,
    ValidationStatus,
)
from src.packages.shared.mutation_service import (
    GitMutationService,
    GitMutationServiceProtocol,
)
from src.packages.shared.validation_service import ValidationService

logger = get_logger("akesis.remediation_orchestrator")


class OrchestrationError(Exception):
    """Base exception for pipeline orchestration failures."""

    pass


class DiagnosticServiceProtocol(Protocol):
    async def diagnose_failure(
        self, context: FailureContext, evidence_package: EvidencePackage | None = None
    ) -> DiagnosticResult: ...


class ContextResolverProtocol(Protocol):
    def resolve_context(
        self, failure_context: FailureContext, repo_root: Any = None
    ) -> EvidencePackage: ...


class FixProposalServiceProtocol(Protocol):
    async def generate_fix_proposal(
        self,
        context: FailureContext,
        evidence_package: EvidencePackage,
        diagnostic_result: DiagnosticResult | None = None,
    ) -> FixProposal: ...


class ValidationServiceProtocol(Protocol):
    async def validate_fix(
        self,
        proposal: FixProposal,
        context: FailureContext,
        repo_root: Any = None,
    ) -> ValidationResult: ...


class ApprovalServiceProtocol(Protocol):
    async def request_approval(
        self,
        context: FailureContext,
        proposal: FixProposal,
        validation: ValidationResult,
    ) -> ApprovalRecord: ...


@asynccontextmanager
async def default_orchestrator_repositories() -> AsyncIterator[
    tuple[
        ApprovalRepositoryProtocol,
        MutationRepositoryProtocol,
        PipelineRepositoryProtocol,
    ]
]:
    """Default repository factory yielding approval, mutation, and pipeline repositories."""
    factory = get_session_factory()
    async with factory() as session:
        yield (
            ApprovalRepository(session),
            MutationRepository(session),
            PipelineRepository(session),
        )


class RemediationOrchestratorProtocol(Protocol):
    """Protocol for end-to-end remediation pipeline orchestration."""

    async def process_failure(self, context: FailureContext) -> PipelineRecord:
        """Executes failure ingestion through diagnostic, fix, validation, and approval gate."""
        ...

    async def resume_approval(
        self,
        approval_id: str,
    ) -> PipelineRecord:
        """Resumes pipeline after human decision is recorded, executing mutation if approved."""
        ...


class RemediationOrchestrator:
    """Coordinates end-to-end CI remediation stages with human authorization and durable state."""

    def __init__(
        self,
        repository_factory: (
            Callable[
                [],
                AbstractAsyncContextManager[
                    tuple[
                        ApprovalRepositoryProtocol,
                        MutationRepositoryProtocol,
                        PipelineRepositoryProtocol,
                    ]
                ],
            ]
            | None
        ) = None,
        diagnostic_service: DiagnosticServiceProtocol | None = None,
        context_resolver: ContextResolverProtocol | None = None,
        fix_service: FixProposalServiceProtocol | None = None,
        validation_service: ValidationServiceProtocol | None = None,
        approval_service: ApprovalServiceProtocol | None = None,
        mutation_service: GitMutationServiceProtocol | None = None,
    ) -> None:
        self.repository_factory = repository_factory or default_orchestrator_repositories
        self.diagnostic_service = diagnostic_service or DiagnosticService()
        self.context_resolver = context_resolver or CodebaseContextResolver()
        self.fix_service = fix_service or FixProposalService()
        self.validation_service = validation_service or ValidationService()
        self.approval_service = approval_service or ApprovalService()
        self.mutation_service = mutation_service or GitMutationService()

    async def process_failure(self, context: FailureContext) -> PipelineRecord:
        """Executes failure ingestion through diagnostic, fix, validation, and approval gate."""
        pipeline_id = f"pipe_{context.incident_id}"
        now = datetime.now(UTC)

        async with self.repository_factory() as (_, _, pipe_repo):
            # 1. Idempotency check: Return existing pipeline if already created
            existing = await pipe_repo.get_by_incident_id(context.incident_id)
            if existing is not None:
                logger.info(
                    "pipeline_already_exists_for_incident",
                    incident_id=context.incident_id,
                    status=existing.status.value,
                )
                return existing

            record = PipelineRecord(
                pipeline_id=pipeline_id,
                incident_id=context.incident_id,
                repository_owner=context.repository_owner,
                repository_name=context.repository_name,
                run_id=context.run_id,
                commit_sha=context.commit_sha,
                status=PipelineStatus.RECEIVED,
                failure_context_json=context.model_dump_json(),
                created_at=now,
                updated_at=now,
            )
            await pipe_repo.create_pipeline(record)

        logger.info(
            "pipeline_started",
            pipeline_id=pipeline_id,
            incident_id=context.incident_id,
            category=context.signal.category,
        )

        try:
            # 2. Stage: Diagnosis
            async with self.repository_factory() as (_, _, pipe_repo):
                await pipe_repo.update_pipeline_state(pipeline_id, PipelineStatus.DIAGNOSING)

            diag_result = await self.diagnostic_service.diagnose_failure(context=context)
            diag_id = f"diag_{context.incident_id}"

            if not diag_result.proposal.is_fixable:
                err_msg = (
                    f"Diagnosis concluded issue is not fixable: {diag_result.proposal.root_cause}"
                )
                async with self.repository_factory() as (_, _, pipe_repo):
                    updated = await pipe_repo.update_pipeline_state(
                        pipeline_id,
                        PipelineStatus.FAILED,
                        diagnosis_id=diag_id,
                        failure_reason=err_msg,
                    )
                logger.info("pipeline_halted_unfixable", incident_id=context.incident_id)
                return updated or record

            # 3. Stage: Codebase Context Resolution
            evidence_pkg = self.context_resolver.resolve_context(
                failure_context=context,
            )

            # 4. Stage: Fix Synthesis
            async with self.repository_factory() as (_, _, pipe_repo):
                await pipe_repo.update_pipeline_state(
                    pipeline_id,
                    PipelineStatus.PROPOSING,
                    diagnosis_id=diag_id,
                )

            fix_prop = await self.fix_service.generate_fix_proposal(
                context=context,
                evidence_package=evidence_pkg,
                diagnostic_result=diag_result,
            )

            if not fix_prop.is_valid or fix_prop.status != "proposed":
                reasons = ", ".join(fix_prop.rejection_reasons) or "Proposal invalid"
                err_msg = f"Fix proposal rejected or ineligible: {reasons}"
                async with self.repository_factory() as (_, _, pipe_repo):
                    updated = await pipe_repo.update_pipeline_state(
                        pipeline_id,
                        PipelineStatus.REJECTED,
                        diagnosis_id=diag_id,
                        proposal_id=fix_prop.proposal_id,
                        failure_reason=err_msg,
                        proposal_json=fix_prop.model_dump_json(),
                    )
                logger.warning(
                    "pipeline_halted_invalid_proposal",
                    incident_id=context.incident_id,
                    reasons=fix_prop.rejection_reasons,
                )
                return updated or record

            # 5. Stage: Sandbox Validation
            async with self.repository_factory() as (_, _, pipe_repo):
                await pipe_repo.update_pipeline_state(
                    pipeline_id,
                    PipelineStatus.VALIDATING,
                    proposal_id=fix_prop.proposal_id,
                    proposal_json=fix_prop.model_dump_json(),
                )

            val_result = await self.validation_service.validate_fix(
                proposal=fix_prop,
                context=context,
            )

            if val_result.status != ValidationStatus.PASSED or val_result.exit_code != 0:
                err_msg = (
                    f"Sandbox validation failed: status={val_result.status.value}, "
                    f"exit_code={val_result.exit_code}, error={val_result.stderr.strip()}"
                )
                async with self.repository_factory() as (_, _, pipe_repo):
                    updated = await pipe_repo.update_pipeline_state(
                        pipeline_id,
                        PipelineStatus.FAILED,
                        proposal_id=fix_prop.proposal_id,
                        failure_reason=err_msg,
                        proposal_json=fix_prop.model_dump_json(),
                        validation_json=val_result.model_dump_json(),
                    )
                logger.warning(
                    "pipeline_halted_validation_failed",
                    incident_id=context.incident_id,
                    status=val_result.status.value,
                    exit_code=val_result.exit_code,
                )
                return updated or record

            # 6. Stage: Request Human Authorization (Post to Slack)
            appr_record = await self.approval_service.request_approval(
                context=context,
                proposal=fix_prop,
                validation=val_result,
            )

            async with self.repository_factory() as (_, _, pipe_repo):
                updated = await pipe_repo.update_pipeline_state(
                    pipeline_id,
                    PipelineStatus.AWAITING_APPROVAL,
                    diagnosis_id=diag_id,
                    proposal_id=fix_prop.proposal_id,
                    approval_id=appr_record.approval_id,
                    failure_context_json=context.model_dump_json(),
                    proposal_json=fix_prop.model_dump_json(),
                    validation_json=val_result.model_dump_json(),
                )

            logger.info(
                "pipeline_awaiting_approval",
                pipeline_id=pipeline_id,
                approval_id=appr_record.approval_id,
            )
            return updated or record

        except Exception as err:
            logger.error(
                "pipeline_unexpected_error", error=str(err), incident_id=context.incident_id
            )
            async with self.repository_factory() as (_, _, pipe_repo):
                updated = await pipe_repo.update_pipeline_state(
                    pipeline_id,
                    PipelineStatus.FAILED,
                    failure_reason=f"Pipeline exception: {err}",
                )
            return updated or record

    async def resume_approval(
        self,
        approval_id: str,
    ) -> PipelineRecord:
        """Resumes pipeline after human decision is recorded, executing mutation if approved.

        Strict Security Invariants:
        - NEVER fabricates fallback proposals or validations.
        - Loads and deserializes exact persisted FailureContext, FixProposal, and ValidationResult.
        - Verifies exact proposal ID, commit SHA, and passed validation before mutation.
        """
        async with self.repository_factory() as (appr_repo, mut_repo, pipe_repo):
            # 1. Retrieve authoritative approval state from PostgreSQL
            approval = await appr_repo.get_approval(approval_id)
            if approval is None:
                raise OrchestrationError(f"Approval record '{approval_id}' not found.")

            pipeline = await pipe_repo.get_by_approval_id(approval_id)
            if pipeline is None:
                pipeline = await pipe_repo.get_by_incident_id(approval.incident_id)

            if pipeline is None:
                raise OrchestrationError(f"Pipeline record for approval '{approval_id}' not found.")

            # If already completed or mutating, maintain idempotency
            if pipeline.status in (PipelineStatus.COMPLETED, PipelineStatus.MUTATING):
                logger.info(
                    "pipeline_already_processed_or_mutating",
                    pipeline_id=pipeline.pipeline_id,
                    status=pipeline.status.value,
                )
                return pipeline

            # 2. Check Decision
            if approval.status == ApprovalStatus.REJECTED:
                updated = await pipe_repo.update_pipeline_state(
                    pipeline.pipeline_id,
                    PipelineStatus.REJECTED,
                    failure_reason=f"Rejected by reviewer: {approval.decided_by or 'reviewer'}",
                )
                logger.info("pipeline_remediation_rejected", approval_id=approval_id)
                return updated or pipeline

            if approval.status != ApprovalStatus.APPROVED:
                raise OrchestrationError(
                    f"Approval record status must be 'approved' (got {approval.status.value})."
                )

            # 3. Retrieve and deserialize authoritative persisted context, proposal, and validation
            if not pipeline.failure_context_json:
                raise OrchestrationError("Durable failure context missing from pipeline record.")
            if not pipeline.proposal_json:
                raise OrchestrationError("Durable fix proposal missing from pipeline record.")
            if not pipeline.validation_json:
                raise OrchestrationError("Durable validation result missing from pipeline record.")

            try:
                context = FailureContext.model_validate_json(pipeline.failure_context_json)
                proposal = FixProposal.model_validate_json(pipeline.proposal_json)
                validation = ValidationResult.model_validate_json(pipeline.validation_json)
            except Exception as err:
                raise OrchestrationError(
                    f"Failed to deserialize durable pipeline objects: {err}"
                ) from err

            # 4. Strict Security Verification of Invariants
            if approval.proposal_id != proposal.proposal_id:
                raise OrchestrationError(
                    f"Approval proposal_id '{approval.proposal_id}' does not match "
                    f"persisted proposal_id '{proposal.proposal_id}'"
                )
            if approval.commit_sha != proposal.commit_sha:
                raise OrchestrationError(
                    f"Approval commit_sha '{approval.commit_sha}' does not match "
                    f"persisted proposal commit_sha '{proposal.commit_sha}'"
                )
            if context.commit_sha != proposal.commit_sha:
                raise OrchestrationError(
                    f"Context commit_sha '{context.commit_sha}' does not match "
                    f"persisted proposal commit_sha '{proposal.commit_sha}'"
                )
            if not proposal.is_valid or proposal.status != "proposed":
                raise OrchestrationError(f"Cannot mutate invalid proposal '{proposal.proposal_id}'")
            if validation.status != ValidationStatus.PASSED or validation.exit_code != 0:
                raise OrchestrationError(
                    f"Cannot mutate unvalidated proposal '{proposal.proposal_id}'"
                )
            if validation.proposal_id != proposal.proposal_id:
                raise OrchestrationError("Validation result does not match proposal ID")

            # 5. Update pipeline state to APPROVED -> MUTATING
            await pipe_repo.update_pipeline_state(
                pipeline.pipeline_id,
                PipelineStatus.MUTATING,
            )

        # 6. Execute controlled Git Mutation & PR creation using exact persisted objects
        try:
            mutation_rec = await self.mutation_service.create_pull_request(
                context=context,
                proposal=proposal,
                validation=validation,
                approval=approval,
            )

            async with self.repository_factory() as (_, _, pipe_repo):
                updated = await pipe_repo.update_pipeline_state(
                    pipeline.pipeline_id,
                    PipelineStatus.COMPLETED,
                    mutation_id=mutation_rec.mutation_id,
                    pr_number=mutation_rec.pr_number,
                    pr_url=mutation_rec.pr_url,
                )
            logger.info(
                "pipeline_completed_successfully",
                pipeline_id=pipeline.pipeline_id,
                pr_number=mutation_rec.pr_number,
            )
            return updated or pipeline

        except Exception as err:
            logger.error(
                "pipeline_mutation_failed", error=str(err), pipeline_id=pipeline.pipeline_id
            )
            async with self.repository_factory() as (_, _, pipe_repo):
                updated = await pipe_repo.update_pipeline_state(
                    pipeline.pipeline_id,
                    PipelineStatus.FAILED,
                    failure_reason=f"Mutation failed: {err}",
                )
            return updated or pipeline
