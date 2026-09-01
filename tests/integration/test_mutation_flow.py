from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.packages.database.repositories import ApprovalRepository, MutationRepository
from src.packages.shared.config import settings
from src.packages.shared.models import (
    ApprovalRecord,
    ApprovalStatus,
    FailureCategory,
    FailureContext,
    FailureSignal,
    FixProposal,
    MutationStatus,
    ValidationCommand,
    ValidationResult,
    ValidationStatus,
    WorkflowRunConclusion,
)
from src.packages.shared.mutation_service import GitMutationService


class MockIntegrationGitHubClient:
    def __init__(self) -> None:
        self.prs: list[dict[str, Any]] = []

    async def get_workflow_run(self, owner: str, repo: str, run_id: int) -> dict[str, Any]:
        return {}

    async def get_workflow_run_logs(self, owner: str, repo: str, run_id: int) -> str:
        return ""

    async def get_commit(self, owner: str, repo: str, commit_sha: str) -> dict[str, Any]:
        return {"sha": commit_sha}

    async def create_pull_request(
        self, owner: str, repo: str, title: str, body: str, head: str, base: str
    ) -> dict[str, Any]:
        data = {
            "number": 108,
            "html_url": f"https://github.com/{owner}/{repo}/pull/108",
            "title": title,
            "head": {"sha": "commit_sha_456"},
        }
        self.prs.append(data)
        return data

    async def find_pull_request(
        self, owner: str, repo: str, head_branch: str, base_branch: str
    ) -> dict[str, Any] | None:
        return None


class MockIntegrationRepoCheckout:
    def __init__(self, target_dir: Path) -> None:
        self.target_dir = target_dir

    def checkout_commit(
        self, repo_owner: str, repo_name: str, commit_sha: str, clone_url: str | None = None
    ) -> Path:
        return self.target_dir


class MockIntegrationSandboxRunner:
    async def run_validation(
        self,
        repo_source_dir: Path,
        patch_diff: str,
        command: ValidationCommand,
        timeout_seconds: float | None = None,
    ) -> Any:
        from src.packages.sdk.sandbox_runner import ValidationExecutionResult

        return ValidationExecutionResult(
            exit_code=0,
            stdout="1 passed",
            stderr="",
            duration_ms=15.0,
            timed_out=False,
        )


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool, echo=False)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def mock_git_repo(tmp_path: Path) -> tuple[Path, str]:
    import git

    repo_dir = tmp_path / "integration_repo"
    repo_dir.mkdir()
    r = git.Repo.init(repo_dir)
    f = repo_dir / "calc.py"
    f.write_text("def add(a, b): return a + b\n", encoding="utf-8")
    r.index.add(["calc.py"])
    c = r.index.commit("initial commit")
    return repo_dir, c.hexsha


@pytest.mark.asyncio
async def test_full_mutation_flow_with_postgres(
    session_factory: async_sessionmaker[AsyncSession],
    mock_git_repo: tuple[Path, str],
) -> None:
    repo_dir, commit_sha = mock_git_repo
    now = datetime.now(UTC)
    unique_id = f"{now.timestamp():.6f}".replace(".", "_")

    # 1. Create durable approved approval record in PostgreSQL
    approval_rec = ApprovalRecord(
        approval_id=f"appr_mut_{unique_id}",
        incident_id=f"inc_mut_{unique_id}",
        proposal_id=f"prop_mut_{unique_id}",
        commit_sha=commit_sha,
        status=ApprovalStatus.APPROVED,
        decided_by="senior_engineer",
        decided_at=now,
        requested_at=now,
        created_at=now,
        updated_at=now,
    )

    async with session_factory() as session:
        appr_repo = ApprovalRepository(session)
        await appr_repo.create_approval(approval_rec)

    context = FailureContext(
        incident_id=approval_rec.incident_id,
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=777,
        workflow_name="CI",
        commit_sha=commit_sha,
        branch="main",
        run_url="https://ci",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=FailureCategory.TEST, message="test failure", target_file="calc.py"
        ),
        raw_log_excerpt="err",
    )

    proposal = FixProposal(
        proposal_id=approval_rec.proposal_id,
        incident_id=approval_rec.incident_id,
        commit_sha=commit_sha,
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
        validation_id=f"val_mut_{unique_id}",
        proposal_id=proposal.proposal_id,
        incident_id=context.incident_id,
        commit_sha=commit_sha,
        status=ValidationStatus.PASSED,
        command_executed="pytest",
        exit_code=0,
        duration_ms=25.0,
    )

    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    from src.packages.database.repositories import (
        ApprovalRepositoryProtocol,
        MutationRepositoryProtocol,
    )

    @asynccontextmanager
    async def scoped_repo_factory() -> AsyncIterator[
        tuple[ApprovalRepositoryProtocol, MutationRepositoryProtocol]
    ]:
        async with session_factory() as session:
            yield (ApprovalRepository(session), MutationRepository(session))

    gh_client = MockIntegrationGitHubClient()
    service = GitMutationService(
        repository_factory=scoped_repo_factory,
        github_client=gh_client,
        repo_checkout=MockIntegrationRepoCheckout(repo_dir),
        sandbox_runner=MockIntegrationSandboxRunner(),
    )

    # 2. Execute mutation pipeline
    record = await service.create_pull_request(context, proposal, validation, approval_rec)
    assert record.status == MutationStatus.PR_CREATED
    assert record.pr_number == 108
    assert record.resulting_commit_sha is not None

    # 3. Verify record was durably written to PostgreSQL
    async with session_factory() as session:
        mut_repo = MutationRepository(session)
        persisted = await mut_repo.get_mutation(record.mutation_id)
        assert persisted is not None
        assert persisted.status == MutationStatus.PR_CREATED
        assert persisted.pr_number == 108
        assert persisted.resulting_commit_sha == record.resulting_commit_sha
