"""Mutation against disposable PostgreSQL and the synthetic GitHub API (Phase 1.7).

The service runs through its private body with explicit verified fixtures, as ADR-0010 requires
while the hold is active. Snapshots come from the real engine; no real repository is contacted.
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.packages.database.models import MutationModel
from src.packages.database.repositories import (
    ApprovalRepository,
    ApprovalRepositoryProtocol,
    MutationRepository,
    MutationRepositoryProtocol,
)
from src.packages.shared.config import settings
from src.packages.shared.models import (
    ApprovalRecord,
    ApprovalStatus,
    FailureCategory,
    FailureContext,
    FailureSignal,
    FixProposal,
    MutationStatus,
    RepositoryIdentity,
    ValidationResult,
    ValidationStatus,
    WorkflowRunConclusion,
    WorkflowRunProvenance,
)
from src.packages.shared.mutation_service import GitMutationService, PullRequestError
from src.packages.shared.source_snapshot import SnapshotService, VerifiedSnapshot
from tests.support.fake_github import FakeGitHub, FakeInstallationAuth

REPO_ID = 1001
FULL = "crlabs-ai/akesis"
APPROVED = "a" * 40


class _PassingValidation:
    """Explicit test double for the gated pre-push validation step."""

    async def validate_fix(
        self, proposal: FixProposal, context: FailureContext
    ) -> ValidationResult:
        return ValidationResult(
            validation_id=f"val_pre_push_{proposal.proposal_id}",
            proposal_id=proposal.proposal_id,
            incident_id=context.incident_id,
            commit_sha=proposal.commit_sha,
            status=ValidationStatus.PASSED,
            command_executed="pytest",
            exit_code=0,
            duration_ms=1.0,
        )


class _Preparer:
    def __init__(self, fake: FakeGitHub) -> None:
        self._service = SnapshotService(auth=FakeInstallationAuth(), client_factory=fake.client)

    def prepare(self, token: object) -> AbstractAsyncContextManager[VerifiedSnapshot]:
        return self._service._open_unchecked(token)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool, echo=False)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def snapshot_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "snapshots"
    monkeypatch.setattr(settings, "snapshot_base_dir", str(root))
    return root


async def _seed(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[FailureContext, FixProposal, ValidationResult, ApprovalRecord, Any]:
    now = datetime.now(UTC)
    unique = f"{now.timestamp():.6f}".replace(".", "_")
    incident = f"inc_mut_{unique}"
    approval = ApprovalRecord(
        approval_id=f"appr_mut_{unique}",
        incident_id=incident,
        proposal_id=f"prop_mut_{unique}",
        commit_sha=APPROVED,
        status=ApprovalStatus.APPROVED,
        decided_by="senior_engineer",
        decided_at=now,
        requested_at=now,
        created_at=now,
        updated_at=now,
    )
    async with factory() as session:
        repo = ApprovalRepository(session)
        await repo.create_approval(approval)
        # Production resumes with the record as PostgreSQL returns it, so mirror that exactly.
        loaded = await repo.get_approval(approval.approval_id)
        assert loaded is not None
    provenance = WorkflowRunProvenance(
        delivery_id=f"delivery-{unique}".replace("_", "-"),
        event_repository=RepositoryIdentity(id=REPO_ID, full_name=FULL),
        source_repository=RepositoryIdentity(id=REPO_ID, full_name=FULL),
        head_sha=APPROVED,
        head_branch="main",
        run_id=777,
        run_attempt=1,
        installation_id=42,
        verified_at=now,
    )
    context = FailureContext(
        incident_id=incident,
        provenance=provenance,
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=777,
        workflow_name="CI",
        commit_sha=APPROVED,
        branch="main",
        run_url="https://github.com/crlabs-ai/akesis/actions/runs/777",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=FailureCategory.TEST, message="failure", target_file="calc.py"
        ),
        raw_log_excerpt="err",
    )
    proposal = FixProposal(
        proposal_id=approval.proposal_id,
        incident_id=incident,
        commit_sha=APPROVED,
        status="proposed",
        is_valid=True,
        unified_diff=(
            "--- a/calc.py\n+++ b/calc.py\n@@ -1,1 +1,2 @@\n"
            " def add(a, b): return a + b\n+# comment\n"
        ),
        target_files=["calc.py"],
        rationale="Add comment",
        risk_level="low",
        confidence_score=0.95,
    )
    validation = ValidationResult(
        validation_id=f"val_mut_{unique}",
        proposal_id=proposal.proposal_id,
        incident_id=incident,
        commit_sha=APPROVED,
        status=ValidationStatus.PASSED,
        command_executed="pytest",
        exit_code=0,
        duration_ms=25.0,
    )
    token = SimpleNamespace(
        incident_id=incident,
        pipeline_id=f"pipe_{incident}",
        delivery_id=provenance.delivery_id,
        event_repository_id=REPO_ID,
        source_repository_id=REPO_ID,
        owner="crlabs-ai",
        name="akesis",
        installation_id=42,
        head_sha=APPROVED,
        head_branch="main",
    )
    return context, proposal, validation, loaded, token


def _service(factory: async_sessionmaker[AsyncSession], fake: FakeGitHub) -> GitMutationService:
    @asynccontextmanager
    async def scoped() -> AsyncIterator[
        tuple[ApprovalRepositoryProtocol, MutationRepositoryProtocol]
    ]:
        async with factory() as session:
            yield ApprovalRepository(session), MutationRepository(session)

    service = GitMutationService(
        repository_factory=scoped,
        auth=FakeInstallationAuth(),
        client_factory=fake.client,
        snapshot_service=_Preparer(fake),
    )
    # Controlled pre-push validation double; the real validator is gated by the provenance hold.
    service.validation_service = cast(Any, _PassingValidation())
    return service


def _fake(**overrides: Any) -> FakeGitHub:
    return FakeGitHub(
        repo_id=REPO_ID,
        full_name=FULL,
        files={"calc.py": ("100644", b"def add(a, b): return a + b\n")},
        commit_sha=APPROVED,
        **overrides,
    )


@pytest.mark.asyncio
async def test_full_mutation_flow_with_postgres(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context, proposal, validation, approval, token = await _seed(session_factory)
    fake = _fake()
    record = await _service(session_factory, fake)._create_pull_request_unchecked(
        context, proposal, validation, approval, token
    )
    assert record.status == MutationStatus.PR_CREATED
    assert record.pr_number == 1
    assert record.pr_url == f"https://github.com/{FULL}/pull/1"
    assert fake.pulls[0]["base"]["ref"] == "main"

    async with session_factory() as session:
        persisted = await MutationRepository(session).get_mutation(record.mutation_id)
    assert persisted is not None
    assert persisted.status == MutationStatus.PR_CREATED
    assert persisted.pr_number == 1
    assert persisted.validation_status == ValidationStatus.PASSED
    assert persisted.resulting_commit_sha == fake.refs[record.branch_name]
    assert (persisted.repository_owner, persisted.repository_name) == ("crlabs-ai", "akesis")


@pytest.mark.asyncio
async def test_retry_after_pr_failure_keeps_one_durable_record_and_one_pr(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context, proposal, validation, approval, token = await _seed(session_factory)
    fake = _fake(fail={"pull_create": 502})
    service = _service(session_factory, fake)
    with pytest.raises(PullRequestError):
        await service._create_pull_request_unchecked(context, proposal, validation, approval, token)

    async with session_factory() as session:
        failed = await MutationRepository(session).get_by_proposal_and_commit(
            proposal.proposal_id, APPROVED
        )
    assert failed is not None and failed.status == MutationStatus.FAILED

    fake.fail.clear()
    record = await service._create_pull_request_unchecked(
        context, proposal, validation, approval, token
    )
    again = await service._create_pull_request_unchecked(
        context, proposal, validation, approval, token
    )
    assert record.mutation_id == again.mutation_id == failed.mutation_id
    assert len(fake.pulls) == 1
    assert sum(1 for verb, path, _ in fake.writes if (verb, path) == ("POST", "/git/refs")) == 1
    async with session_factory() as session:
        rows = await session.scalar(
            select(func.count())
            .select_from(MutationModel)
            .where(MutationModel.proposal_id == proposal.proposal_id)
        )
    assert rows == 1
