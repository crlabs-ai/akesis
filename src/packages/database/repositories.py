import json
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.packages.database.models import ApprovalModel, MutationModel, PipelineModel
from src.packages.shared.logging import get_logger
from src.packages.shared.models import (
    ApprovalRecord,
    ApprovalStatus,
    FailureContext,
    FixProposal,
    IncidentDetail,
    IncidentSummary,
    MutationRecord,
    MutationStatus,
    PipelineRecord,
    PipelineStatus,
    PipelineSummary,
    ValidationResult,
    ValidationStatus,
)

logger = get_logger("akesis.database.repository")


class ApprovalRepositoryProtocol(Protocol):
    """Protocol defining persistence operations for human approvals."""

    async def create_approval(self, record: ApprovalRecord) -> ApprovalRecord:
        """Persists a new pending approval record."""
        ...

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        """Retrieves an approval record by ID."""
        ...

    async def get_by_proposal_id(self, proposal_id: str) -> ApprovalRecord | None:
        """Retrieves an approval record by fix proposal ID."""
        ...

    async def record_decision(
        self,
        approval_id: str,
        target_status: ApprovalStatus,
        reviewer: str,
        reason: str | None = None,
    ) -> tuple[ApprovalRecord | None, bool]:
        """Atomically transitions approval status if pending, returning (record, is_duplicate)."""
        ...

    async def expire_approval(self, approval_id: str) -> bool:
        """Transitions a pending approval to expired status."""
        ...


class MutationRepositoryProtocol(Protocol):
    """Protocol defining persistence operations for Git mutations and Pull Requests."""

    async def create_mutation(self, record: MutationRecord) -> MutationRecord:
        """Persists a new mutation record."""
        ...

    async def get_mutation(self, mutation_id: str) -> MutationRecord | None:
        """Retrieves a mutation record by ID."""
        ...

    async def get_by_proposal_and_commit(
        self, proposal_id: str, commit_sha: str
    ) -> MutationRecord | None:
        """Retrieves a mutation record for a specific proposal and base commit SHA."""
        ...

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
        """Updates mutation lifecycle state and metadata."""
        ...


class PipelineRepositoryProtocol(Protocol):
    """Protocol defining persistence operations for end-to-end orchestration pipelines."""

    async def create_pipeline(self, record: PipelineRecord) -> PipelineRecord:
        """Persists a new pipeline record."""
        ...

    async def get_pipeline(self, pipeline_id: str) -> PipelineRecord | None:
        """Retrieves a pipeline record by pipeline_id."""
        ...

    async def get_by_incident_id(self, incident_id: str) -> PipelineRecord | None:
        """Retrieves a pipeline record by incident_id."""
        ...

    async def get_by_approval_id(self, approval_id: str) -> PipelineRecord | None:
        """Retrieves a pipeline record by approval_id."""
        ...

    async def list_recent_pipelines(
        self, limit: int = 20, offset: int = 0
    ) -> list[PipelineSummary]:
        """Retrieves paginated pipeline summaries ordered deterministically by created_at."""
        ...

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
        """Updates pipeline lifecycle state and stage identifiers."""
        ...


class IncidentRepositoryProtocol(Protocol):
    """Protocol defining query operations for incident summaries and details."""

    async def list_incidents(self, limit: int = 20, offset: int = 0) -> list[IncidentSummary]:
        """Retrieves paginated incident summaries ordered deterministically by created_at."""
        ...

    async def get_incident(self, incident_id: str) -> IncidentDetail | None:
        """Retrieves full incident detail by incident_id."""
        ...


class ApprovalRepository:
    """SQLAlchemy async implementation of ApprovalRepositoryProtocol."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_domain(self, model: ApprovalModel) -> ApprovalRecord:
        """Maps ORM model to Pydantic domain model."""
        return ApprovalRecord(
            approval_id=model.approval_id,
            incident_id=model.incident_id,
            diagnosis_id=model.diagnosis_id,
            proposal_id=model.proposal_id,
            commit_sha=model.commit_sha,
            status=ApprovalStatus(model.status),
            slack_channel_id=model.slack_channel_id,
            slack_message_ts=model.slack_message_ts,
            requested_at=model.requested_at,
            decided_at=model.decided_at,
            decided_by=model.reviewer,
            decision_reason=model.reason,
            expires_at=model.expires_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def create_approval(self, record: ApprovalRecord) -> ApprovalRecord:
        """Persists a new approval record in PostgreSQL."""
        model = ApprovalModel(
            approval_id=record.approval_id,
            incident_id=record.incident_id,
            diagnosis_id=record.diagnosis_id,
            proposal_id=record.proposal_id,
            commit_sha=record.commit_sha,
            status=record.status.value,
            slack_channel_id=record.slack_channel_id,
            slack_message_ts=record.slack_message_ts,
            reviewer=record.decided_by,
            reason=record.decision_reason,
            requested_at=record.requested_at,
            expires_at=record.expires_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        logger.info("approval_record_persisted", approval_id=record.approval_id)
        return self._to_domain(model)

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        """Loads approval record from PostgreSQL by approval_id."""
        stmt = select(ApprovalModel).where(ApprovalModel.approval_id == approval_id)
        res = await self.session.execute(stmt)
        model = res.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_by_proposal_id(self, proposal_id: str) -> ApprovalRecord | None:
        """Loads approval record from PostgreSQL by proposal_id."""
        stmt = select(ApprovalModel).where(ApprovalModel.proposal_id == proposal_id)
        res = await self.session.execute(stmt)
        model = res.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def record_decision(
        self,
        approval_id: str,
        target_status: ApprovalStatus,
        reviewer: str,
        reason: str | None = None,
    ) -> tuple[ApprovalRecord | None, bool]:
        """Executes an atomic conditional update at the database level."""
        now = datetime.now(UTC)

        stmt = (
            update(ApprovalModel)
            .where(
                ApprovalModel.approval_id == approval_id,
                ApprovalModel.status == ApprovalStatus.PENDING.value,
            )
            .values(
                status=target_status.value,
                reviewer=reviewer,
                reason=reason,
                decided_at=now,
                updated_at=now,
            )
            .returning(ApprovalModel)
        )
        res = await self.session.execute(stmt)
        updated_model = res.scalar_one_or_none()

        if updated_model is not None:
            await self.session.commit()
            logger.info(
                "approval_decision_committed",
                approval_id=approval_id,
                status=target_status.value,
                reviewer=reviewer,
            )
            return self._to_domain(updated_model), False

        current_record = await self.get_approval(approval_id)
        if current_record is None:
            return None, False

        if current_record.status == target_status:
            logger.info(
                "approval_decision_idempotent_duplicate",
                approval_id=approval_id,
                status=current_record.status.value,
            )
            return current_record, True

        return current_record, False

    async def expire_approval(self, approval_id: str) -> bool:
        """Transitions a pending approval to expired status."""
        now = datetime.now(UTC)
        stmt = (
            update(ApprovalModel)
            .where(
                ApprovalModel.approval_id == approval_id,
                ApprovalModel.status == ApprovalStatus.PENDING.value,
            )
            .values(
                status=ApprovalStatus.EXPIRED.value,
                updated_at=now,
            )
            .returning(ApprovalModel.approval_id)
        )
        res = await self.session.execute(stmt)
        updated_id = res.scalar_one_or_none()
        await self.session.commit()
        return updated_id is not None


class MutationRepository:
    """SQLAlchemy async implementation of MutationRepositoryProtocol."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_domain(self, model: MutationModel) -> MutationRecord:
        """Maps ORM model to Pydantic domain model."""
        return MutationRecord(
            mutation_id=model.mutation_id,
            proposal_id=model.proposal_id,
            approval_id=model.approval_id,
            incident_id=model.incident_id,
            repository_owner=model.repository_owner,
            repository_name=model.repository_name,
            base_commit_sha=model.base_commit_sha,
            branch_name=model.branch_name,
            resulting_commit_sha=model.resulting_commit_sha,
            validation_status=(
                ValidationStatus(model.validation_status) if model.validation_status else None
            ),
            pr_number=model.pr_number,
            pr_url=model.pr_url,
            status=MutationStatus(model.status),
            failure_reason=model.failure_reason,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def create_mutation(self, record: MutationRecord) -> MutationRecord:
        """Persists a new mutation record."""
        model = MutationModel(
            mutation_id=record.mutation_id,
            proposal_id=record.proposal_id,
            approval_id=record.approval_id,
            incident_id=record.incident_id,
            repository_owner=record.repository_owner,
            repository_name=record.repository_name,
            base_commit_sha=record.base_commit_sha,
            branch_name=record.branch_name,
            resulting_commit_sha=record.resulting_commit_sha,
            validation_status=(
                record.validation_status.value if record.validation_status else None
            ),
            pr_number=record.pr_number,
            pr_url=record.pr_url,
            status=record.status.value,
            failure_reason=record.failure_reason,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        logger.info("mutation_record_created", mutation_id=record.mutation_id)
        return self._to_domain(model)

    async def get_mutation(self, mutation_id: str) -> MutationRecord | None:
        """Loads mutation record from PostgreSQL by mutation_id."""
        stmt = select(MutationModel).where(MutationModel.mutation_id == mutation_id)
        res = await self.session.execute(stmt)
        model = res.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_by_proposal_and_commit(
        self, proposal_id: str, commit_sha: str
    ) -> MutationRecord | None:
        """Loads mutation record by proposal_id and base_commit_sha."""
        stmt = select(MutationModel).where(
            MutationModel.proposal_id == proposal_id,
            MutationModel.base_commit_sha == commit_sha,
        )
        res = await self.session.execute(stmt)
        model = res.scalar_one_or_none()
        return self._to_domain(model) if model else None

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
        """Updates mutation lifecycle state and metadata."""
        now = datetime.now(UTC)
        values: dict[str, object] = {
            "status": status.value,
            "updated_at": now,
        }
        if resulting_commit_sha is not None:
            values["resulting_commit_sha"] = resulting_commit_sha
        if validation_status is not None:
            values["validation_status"] = validation_status.value
        if pr_number is not None:
            values["pr_number"] = pr_number
        if pr_url is not None:
            values["pr_url"] = pr_url
        if failure_reason is not None:
            values["failure_reason"] = failure_reason

        stmt = (
            update(MutationModel)
            .where(MutationModel.mutation_id == mutation_id)
            .values(**values)
            .returning(MutationModel)
        )
        res = await self.session.execute(stmt)
        model = res.scalar_one_or_none()
        if model is not None:
            await self.session.commit()
            return self._to_domain(model)
        return None


class PipelineRepository:
    """SQLAlchemy async implementation of PipelineRepositoryProtocol."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_domain(self, model: PipelineModel) -> PipelineRecord:
        """Maps ORM model to Pydantic domain model."""
        return PipelineRecord(
            pipeline_id=model.pipeline_id,
            incident_id=model.incident_id,
            repository_owner=model.repository_owner,
            repository_name=model.repository_name,
            run_id=model.run_id,
            commit_sha=model.commit_sha,
            status=PipelineStatus(model.status),
            diagnosis_id=model.diagnosis_id,
            proposal_id=model.proposal_id,
            approval_id=model.approval_id,
            mutation_id=model.mutation_id,
            pr_number=model.pr_number,
            pr_url=model.pr_url,
            failure_reason=model.failure_reason,
            failure_context_json=model.failure_context_json,
            proposal_json=model.proposal_json,
            validation_json=model.validation_json,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def create_pipeline(self, record: PipelineRecord) -> PipelineRecord:
        """Persists a new pipeline record."""
        model = PipelineModel(
            pipeline_id=record.pipeline_id,
            incident_id=record.incident_id,
            repository_owner=record.repository_owner,
            repository_name=record.repository_name,
            run_id=record.run_id,
            commit_sha=record.commit_sha,
            status=record.status.value,
            diagnosis_id=record.diagnosis_id,
            proposal_id=record.proposal_id,
            approval_id=record.approval_id,
            mutation_id=record.mutation_id,
            pr_number=record.pr_number,
            pr_url=record.pr_url,
            failure_reason=record.failure_reason,
            failure_context_json=record.failure_context_json,
            proposal_json=record.proposal_json,
            validation_json=record.validation_json,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        logger.info("pipeline_record_created", pipeline_id=record.pipeline_id)
        return self._to_domain(model)

    async def get_pipeline(self, pipeline_id: str) -> PipelineRecord | None:
        """Loads pipeline record from PostgreSQL by pipeline_id."""
        stmt = select(PipelineModel).where(PipelineModel.pipeline_id == pipeline_id)
        res = await self.session.execute(stmt)
        model = res.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_by_incident_id(self, incident_id: str) -> PipelineRecord | None:
        """Loads pipeline record by incident_id."""
        stmt = select(PipelineModel).where(PipelineModel.incident_id == incident_id)
        res = await self.session.execute(stmt)
        model = res.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_by_approval_id(self, approval_id: str) -> PipelineRecord | None:
        """Loads pipeline record by approval_id."""
        stmt = select(PipelineModel).where(PipelineModel.approval_id == approval_id)
        res = await self.session.execute(stmt)
        model = res.scalar_one_or_none()
        return self._to_domain(model) if model else None

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
        """Updates pipeline lifecycle state and stage identifiers."""
        now = datetime.now(UTC)
        values: dict[str, object] = {
            "status": status.value,
            "updated_at": now,
        }
        if diagnosis_id is not None:
            values["diagnosis_id"] = diagnosis_id
        if proposal_id is not None:
            values["proposal_id"] = proposal_id
        if approval_id is not None:
            values["approval_id"] = approval_id
        if mutation_id is not None:
            values["mutation_id"] = mutation_id
        if pr_number is not None:
            values["pr_number"] = pr_number
        if pr_url is not None:
            values["pr_url"] = pr_url
        if failure_reason is not None:
            values["failure_reason"] = failure_reason
        if failure_context_json is not None:
            values["failure_context_json"] = failure_context_json
        if proposal_json is not None:
            values["proposal_json"] = proposal_json
        if validation_json is not None:
            values["validation_json"] = validation_json

        stmt = (
            update(PipelineModel)
            .where(PipelineModel.pipeline_id == pipeline_id)
            .values(**values)
            .returning(PipelineModel)
        )
        res = await self.session.execute(stmt)
        model = res.scalar_one_or_none()
        if model is not None:
            await self.session.commit()
            return self._to_domain(model)
        return None

    def _to_summary(self, model: PipelineModel) -> PipelineSummary:
        return PipelineSummary(
            pipeline_id=model.pipeline_id,
            incident_id=model.incident_id,
            repository_owner=model.repository_owner,
            repository_name=model.repository_name,
            run_id=model.run_id,
            commit_sha=model.commit_sha,
            status=PipelineStatus(model.status),
            diagnosis_id=model.diagnosis_id,
            proposal_id=model.proposal_id,
            approval_id=model.approval_id,
            mutation_id=model.mutation_id,
            pr_number=model.pr_number,
            pr_url=model.pr_url,
            failure_reason=model.failure_reason,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def list_recent_pipelines(
        self, limit: int = 20, offset: int = 0
    ) -> list[PipelineSummary]:
        """Retrieves paginated pipeline summaries ordered deterministically by created_at."""
        stmt = (
            select(PipelineModel)
            .order_by(PipelineModel.created_at.desc(), PipelineModel.pipeline_id.desc())
            .limit(limit)
            .offset(offset)
        )
        res = await self.session.execute(stmt)
        models = res.scalars().all()
        return [self._to_summary(m) for m in models]


class IncidentRepository:
    """SQLAlchemy async implementation of IncidentRepositoryProtocol."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_incidents(self, limit: int = 20, offset: int = 0) -> list[IncidentSummary]:
        """Retrieves paginated incident summaries ordered deterministically by created_at."""
        stmt = (
            select(PipelineModel, ApprovalModel.status.label("approval_status"))
            .outerjoin(ApprovalModel, PipelineModel.approval_id == ApprovalModel.approval_id)
            .order_by(PipelineModel.created_at.desc(), PipelineModel.pipeline_id.desc())
            .limit(limit)
            .offset(offset)
        )
        res = await self.session.execute(stmt)
        rows = res.all()

        summaries: list[IncidentSummary] = []
        for pipeline_model, approval_status in rows:
            workflow_name = "Unknown Workflow"
            branch = "unknown"
            category = "unknown"
            error_type = "UnknownError"
            failure_message = pipeline_model.failure_reason or ""
            target_file = None
            target_line = None

            if pipeline_model.failure_context_json:
                try:
                    fc_data = json.loads(pipeline_model.failure_context_json)
                    workflow_name = fc_data.get("workflow_name") or workflow_name
                    branch = fc_data.get("branch") or branch
                    signal = fc_data.get("signal") or {}
                    category = signal.get("category") or category
                    error_type = signal.get("error_type") or error_type
                    failure_message = (
                        signal.get("message")
                        or signal.get("failure_message")
                        or (pipeline_model.failure_reason or "")
                    )
                    target_file = signal.get("target_file")
                    target_line = signal.get("target_line")
                except Exception:
                    pass

            summaries.append(
                IncidentSummary(
                    incident_id=pipeline_model.incident_id,
                    repository=f"{pipeline_model.repository_owner}/{pipeline_model.repository_name}",
                    run_id=pipeline_model.run_id,
                    commit_sha=pipeline_model.commit_sha,
                    workflow_name=workflow_name,
                    branch=branch,
                    category=category,
                    error_type=error_type,
                    failure_message=failure_message,
                    current_stage=pipeline_model.status,
                    approval_status=approval_status,
                    approval_id=pipeline_model.approval_id,
                    target_file=target_file,
                    target_line=target_line,
                    pr_number=pipeline_model.pr_number,
                    pr_url=pipeline_model.pr_url,
                    created_at=pipeline_model.created_at,
                    updated_at=pipeline_model.updated_at,
                )
            )
        return summaries

    async def get_incident(self, incident_id: str) -> IncidentDetail | None:
        """Retrieves full incident detail by incident_id."""
        stmt = (
            select(PipelineModel, ApprovalModel.status.label("approval_status"))
            .outerjoin(ApprovalModel, PipelineModel.approval_id == ApprovalModel.approval_id)
            .where(PipelineModel.incident_id == incident_id)
        )
        res = await self.session.execute(stmt)
        row = res.first()
        if row is None:
            return None

        pipeline_model, approval_status = row

        failure_context: FailureContext | None = None
        traceback_excerpt: str | None = None
        workflow_name = "Unknown Workflow"
        branch = "unknown"
        category = "unknown"
        error_type = "UnknownError"
        failure_message = pipeline_model.failure_reason or ""
        target_file = None
        target_line = None

        if pipeline_model.failure_context_json:
            try:
                failure_context = FailureContext.model_validate_json(
                    pipeline_model.failure_context_json
                )
                workflow_name = failure_context.workflow_name
                branch = failure_context.branch
                category = (
                    failure_context.signal.category.value
                    if hasattr(failure_context.signal.category, "value")
                    else str(failure_context.signal.category)
                )
                error_type = failure_context.signal.error_type
                failure_message = failure_context.signal.message or (
                    pipeline_model.failure_reason or ""
                )
                target_file = failure_context.signal.target_file
                target_line = failure_context.signal.target_line
                traceback_excerpt = (
                    failure_context.raw_log_excerpt
                    or failure_context.signal.extracted_snippet
                    or None
                )
            except Exception:
                try:
                    fc_data = json.loads(pipeline_model.failure_context_json)
                    workflow_name = fc_data.get("workflow_name") or workflow_name
                    branch = fc_data.get("branch") or branch
                    signal = fc_data.get("signal") or {}
                    category = signal.get("category") or category
                    error_type = signal.get("error_type") or error_type
                    failure_message = (
                        signal.get("message")
                        or signal.get("failure_message")
                        or (pipeline_model.failure_reason or "")
                    )
                    target_file = signal.get("target_file")
                    target_line = signal.get("target_line")
                    traceback_excerpt = fc_data.get("raw_log_excerpt") or signal.get(
                        "extracted_snippet"
                    )
                except Exception:
                    pass

        fix_proposal: FixProposal | None = None
        diff_content: str | None = None
        if pipeline_model.proposal_json:
            try:
                fix_proposal = FixProposal.model_validate_json(pipeline_model.proposal_json)
                diff_content = fix_proposal.unified_diff or None
            except Exception:
                try:
                    prop_data = json.loads(pipeline_model.proposal_json)
                    diff_content = prop_data.get("unified_diff")
                except Exception:
                    pass

        validation_result: ValidationResult | None = None
        sandbox_command: str | None = None
        sandbox_exit_code: int | None = None
        sandbox_stdout: str | None = None
        sandbox_stderr: str | None = None
        if pipeline_model.validation_json:
            try:
                validation_result = ValidationResult.model_validate_json(
                    pipeline_model.validation_json
                )
                sandbox_command = validation_result.command_executed
                sandbox_exit_code = validation_result.exit_code
                sandbox_stdout = validation_result.stdout
                sandbox_stderr = validation_result.stderr
            except Exception:
                try:
                    val_data = json.loads(pipeline_model.validation_json)
                    sandbox_command = val_data.get("command_executed")
                    sandbox_exit_code = val_data.get("exit_code")
                    sandbox_stdout = val_data.get("stdout")
                    sandbox_stderr = val_data.get("stderr")
                except Exception:
                    pass

        return IncidentDetail(
            incident_id=pipeline_model.incident_id,
            repository=f"{pipeline_model.repository_owner}/{pipeline_model.repository_name}",
            run_id=pipeline_model.run_id,
            commit_sha=pipeline_model.commit_sha,
            workflow_name=workflow_name,
            branch=branch,
            category=category,
            error_type=error_type,
            failure_message=failure_message,
            current_stage=pipeline_model.status,
            approval_status=approval_status,
            approval_id=pipeline_model.approval_id,
            target_file=target_file,
            target_line=target_line,
            pr_number=pipeline_model.pr_number,
            pr_url=pipeline_model.pr_url,
            created_at=pipeline_model.created_at,
            updated_at=pipeline_model.updated_at,
            diff_content=diff_content,
            failure_context=failure_context,
            fix_proposal=fix_proposal,
            validation_result=validation_result,
            traceback_excerpt=traceback_excerpt,
            sandbox_command=sandbox_command,
            sandbox_exit_code=sandbox_exit_code,
            sandbox_stdout=sandbox_stdout,
            sandbox_stderr=sandbox_stderr,
        )
