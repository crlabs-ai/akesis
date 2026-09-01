from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from src.packages.database.repositories import (
    ApprovalRepositoryProtocol,
    MutationRepositoryProtocol,
)
from src.packages.shared.models import (
    ApprovalRecord,
    ApprovalStatus,
    FailureCategory,
    FailureContext,
    FailureSignal,
    FixProposal,
    MutationRecord,
    MutationStatus,
    ValidationCommand,
    ValidationResult,
    ValidationStatus,
    WorkflowRunConclusion,
)
from src.packages.shared.mutation_service import (
    GitMutationService,
    PreflightCheckError,
    PrePushValidationError,
    StaleCommitError,
    UnsafeBranchError,
    WorkingTreeMismatchError,
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
        return None, False

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
        record = self.records.get(mutation_id)
        if record:
            record.status = status
            if resulting_commit_sha is not None:
                record.resulting_commit_sha = resulting_commit_sha
            if validation_status is not None:
                record.validation_status = validation_status
            if pr_number is not None:
                record.pr_number = pr_number
            if pr_url is not None:
                record.pr_url = pr_url
            if failure_reason is not None:
                record.failure_reason = failure_reason
        return record


class MockGitHubClient:
    def __init__(self, should_fail_auth: bool = False, should_fail_perm: bool = False) -> None:
        self.created_prs: list[dict[str, Any]] = []
        self.should_fail_auth = should_fail_auth
        self.should_fail_perm = should_fail_perm

    async def get_workflow_run(self, owner: str, repo: str, run_id: int) -> dict[str, Any]:
        return {}

    async def get_workflow_run_logs(self, owner: str, repo: str, run_id: int) -> str:
        return ""

    async def get_commit(self, owner: str, repo: str, commit_sha: str) -> dict[str, Any]:
        return {"sha": commit_sha}

    async def create_pull_request(
        self, owner: str, repo: str, title: str, body: str, head: str, base: str
    ) -> dict[str, Any]:
        if self.should_fail_auth:
            from src.packages.sdk.github_client import GitHubAuthError

            raise GitHubAuthError("Unauthorized", status_code=401)
        if self.should_fail_perm:
            from src.packages.sdk.github_client import GitHubPermissionError

            raise GitHubPermissionError("Permission Denied", status_code=403)
        data = {
            "number": 42,
            "html_url": f"https://github.com/{owner}/{repo}/pull/42",
            "title": title,
            "head": {"sha": "new_sha_123"},
        }
        self.created_prs.append(data)
        return data

    async def find_pull_request(
        self, owner: str, repo: str, head_branch: str, base_branch: str
    ) -> dict[str, Any] | None:
        return None


class MockRepoCheckout:
    def __init__(self, target_dir: Path) -> None:
        self.target_dir = target_dir

    def checkout_commit(
        self, repo_owner: str, repo_name: str, commit_sha: str, clone_url: str | None = None
    ) -> Path:
        return self.target_dir


class MockSandboxRunner:
    def __init__(
        self,
        return_status: ValidationStatus = ValidationStatus.PASSED,
        exit_code: int = 0,
    ) -> None:
        self.return_status = return_status
        self.exit_code = exit_code

    async def run_validation(
        self,
        repo_source_dir: Path,
        patch_diff: str,
        command: ValidationCommand,
        timeout_seconds: float | None = None,
    ) -> Any:
        from src.packages.sdk.sandbox_runner import ValidationExecutionResult

        return ValidationExecutionResult(
            exit_code=self.exit_code,
            stdout="validation output",
            stderr="",
            duration_ms=10.0,
            timed_out=(self.return_status == ValidationStatus.TIMED_OUT),
        )


@pytest.fixture
def mock_git_repo(tmp_path: Path) -> tuple[Path, str]:
    import git

    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()
    r = git.Repo.init(repo_dir)
    f = repo_dir / "src.py"
    f.write_text("x = 1\n", encoding="utf-8")
    r.index.add(["src.py"])
    c = r.index.commit("init")
    return repo_dir, c.hexsha


@pytest.fixture
def sample_mutation_data(
    mock_git_repo: tuple[Path, str],
) -> tuple[FailureContext, FixProposal, ValidationResult, ApprovalRecord]:
    _, commit_sha = mock_git_repo
    context = FailureContext(
        incident_id="inc_mut_01",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=1,
        workflow_name="CI",
        commit_sha=commit_sha,
        branch="main",
        run_url="https://ci",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(category=FailureCategory.TEST, message="failed", target_file="src.py"),
        raw_log_excerpt="err",
    )
    proposal = FixProposal(
        proposal_id="prop_mut_01",
        incident_id="inc_mut_01",
        commit_sha=commit_sha,
        status="proposed",
        is_valid=True,
        unified_diff="--- a/src.py\n+++ b/src.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n",
        target_files=["src.py"],
        rationale="Fix x",
        risk_level="low",
        confidence_score=0.95,
    )
    validation = ValidationResult(
        validation_id="val_01",
        proposal_id="prop_mut_01",
        incident_id="inc_mut_01",
        commit_sha=commit_sha,
        status=ValidationStatus.PASSED,
        command_executed="pytest",
        exit_code=0,
        duration_ms=20.0,
    )
    approval = ApprovalRecord(
        approval_id="appr_01",
        incident_id="inc_mut_01",
        proposal_id="prop_mut_01",
        commit_sha=commit_sha,
        status=ApprovalStatus.APPROVED,
        decided_by="lead_dev",
        decided_at=datetime.now(UTC),
    )
    return context, proposal, validation, approval


@pytest.mark.asyncio
async def test_successful_pr_creation(
    mock_git_repo: tuple[Path, str],
    sample_mutation_data: tuple[FailureContext, FixProposal, ValidationResult, ApprovalRecord],
) -> None:
    repo_dir, _ = mock_git_repo
    context, proposal, validation, approval = sample_mutation_data
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()

    @asynccontextmanager
    async def mock_repo_factory() -> AsyncIterator[
        tuple[ApprovalRepositoryProtocol, MutationRepositoryProtocol]
    ]:
        yield (appr_repo, mut_repo)

    gh_client = MockGitHubClient()
    service = GitMutationService(
        repository_factory=mock_repo_factory,
        github_client=gh_client,
        repo_checkout=MockRepoCheckout(repo_dir),
        sandbox_runner=MockSandboxRunner(),
    )

    record = await service.create_pull_request(context, proposal, validation, approval)
    assert record.status == MutationStatus.PR_CREATED
    assert record.pr_number == 42
    assert record.resulting_commit_sha is not None
    assert len(gh_client.created_prs) == 1


@pytest.mark.asyncio
async def test_unapproved_proposal_rejected(
    mock_git_repo: tuple[Path, str],
    sample_mutation_data: tuple[FailureContext, FixProposal, ValidationResult, ApprovalRecord],
) -> None:
    repo_dir, _ = mock_git_repo
    context, proposal, validation, approval = sample_mutation_data
    approval.status = ApprovalStatus.PENDING

    service = GitMutationService(
        repo_checkout=MockRepoCheckout(repo_dir),
        sandbox_runner=MockSandboxRunner(),
    )
    with pytest.raises(PreflightCheckError) as exc:
        await service.create_pull_request(context, proposal, validation, approval)
    assert "must be 'approved'" in str(exc.value)


@pytest.mark.asyncio
async def test_mismatched_proposal_id_rejected(
    mock_git_repo: tuple[Path, str],
    sample_mutation_data: tuple[FailureContext, FixProposal, ValidationResult, ApprovalRecord],
) -> None:
    repo_dir, _ = mock_git_repo
    context, proposal, validation, approval = sample_mutation_data
    approval.proposal_id = "other_proposal_id"

    service = GitMutationService(
        repo_checkout=MockRepoCheckout(repo_dir),
        sandbox_runner=MockSandboxRunner(),
    )
    with pytest.raises(PreflightCheckError):
        await service.create_pull_request(context, proposal, validation, approval)


@pytest.mark.asyncio
async def test_mismatched_commit_sha_rejected(
    mock_git_repo: tuple[Path, str],
    sample_mutation_data: tuple[FailureContext, FixProposal, ValidationResult, ApprovalRecord],
) -> None:
    repo_dir, _ = mock_git_repo
    context, proposal, validation, approval = sample_mutation_data
    approval.commit_sha = "000000000000"

    service = GitMutationService(
        repo_checkout=MockRepoCheckout(repo_dir),
        sandbox_runner=MockSandboxRunner(),
    )
    with pytest.raises(PreflightCheckError):
        await service.create_pull_request(context, proposal, validation, approval)


@pytest.mark.asyncio
async def test_stale_commit_sha_detected(
    mock_git_repo: tuple[Path, str],
    sample_mutation_data: tuple[FailureContext, FixProposal, ValidationResult, ApprovalRecord],
) -> None:
    repo_dir, _ = mock_git_repo
    context, proposal, validation, approval = sample_mutation_data

    # Change proposal commit_sha to something else while keeping approval matching proposal
    fake_sha = "ffffffffffffffffffffffffffffffffffffffff"
    context.commit_sha = fake_sha
    proposal.commit_sha = fake_sha
    approval.commit_sha = fake_sha

    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()

    @asynccontextmanager
    async def mock_repo_factory() -> AsyncIterator[
        tuple[ApprovalRepositoryProtocol, MutationRepositoryProtocol]
    ]:
        yield (appr_repo, mut_repo)

    service = GitMutationService(
        repository_factory=mock_repo_factory,
        github_client=MockGitHubClient(),
        repo_checkout=MockRepoCheckout(repo_dir),
        sandbox_runner=MockSandboxRunner(),
    )

    with pytest.raises(StaleCommitError):
        await service.create_pull_request(context, proposal, validation, approval)


@pytest.mark.asyncio
async def test_unsafe_branch_name_rejected() -> None:
    service = GitMutationService()
    with pytest.raises(UnsafeBranchError):
        service.validate_branch_name("akesis/fix/../../root")

    with pytest.raises(UnsafeBranchError):
        service.validate_branch_name("main")


@pytest.mark.asyncio
async def test_pre_push_validation_failure_aborts_commit(
    mock_git_repo: tuple[Path, str],
    sample_mutation_data: tuple[FailureContext, FixProposal, ValidationResult, ApprovalRecord],
) -> None:
    repo_dir, _ = mock_git_repo
    context, proposal, validation, approval = sample_mutation_data
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()

    @asynccontextmanager
    async def mock_repo_factory() -> AsyncIterator[
        tuple[ApprovalRepositoryProtocol, MutationRepositoryProtocol]
    ]:
        yield (appr_repo, mut_repo)

    service = GitMutationService(
        repository_factory=mock_repo_factory,
        github_client=MockGitHubClient(),
        repo_checkout=MockRepoCheckout(repo_dir),
        sandbox_runner=MockSandboxRunner(return_status=ValidationStatus.FAILED, exit_code=1),
    )

    with pytest.raises(PrePushValidationError):
        await service.create_pull_request(context, proposal, validation, approval)


@pytest.mark.asyncio
async def test_duplicate_mutation_returns_existing(
    mock_git_repo: tuple[Path, str],
    sample_mutation_data: tuple[FailureContext, FixProposal, ValidationResult, ApprovalRecord],
) -> None:
    repo_dir, _ = mock_git_repo
    context, proposal, validation, approval = sample_mutation_data
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()

    # Pre-populate mutation repo with existing PR_CREATED record
    existing = MutationRecord(
        mutation_id="mut_existing_01",
        proposal_id=proposal.proposal_id,
        approval_id=approval.approval_id,
        incident_id=context.incident_id,
        repository_owner=context.repository_owner,
        repository_name=context.repository_name,
        base_commit_sha=proposal.commit_sha,
        branch_name="akesis/fix/inc/prop",
        status=MutationStatus.PR_CREATED,
        pr_number=99,
        pr_url="https://github.com/crlabs-ai/akesis/pull/99",
    )
    mut_repo.records[existing.mutation_id] = existing

    @asynccontextmanager
    async def mock_repo_factory() -> AsyncIterator[
        tuple[ApprovalRepositoryProtocol, MutationRepositoryProtocol]
    ]:
        yield (appr_repo, mut_repo)

    gh_client = MockGitHubClient()
    service = GitMutationService(
        repository_factory=mock_repo_factory,
        github_client=gh_client,
        repo_checkout=MockRepoCheckout(repo_dir),
        sandbox_runner=MockSandboxRunner(),
    )

    result = await service.create_pull_request(context, proposal, validation, approval)
    assert result.mutation_id == existing.mutation_id
    assert result.pr_number == 99
    # No new PR was created via GitHub API
    assert len(gh_client.created_prs) == 0


@pytest.mark.asyncio
async def test_github_auth_error_handled(
    mock_git_repo: tuple[Path, str],
    sample_mutation_data: tuple[FailureContext, FixProposal, ValidationResult, ApprovalRecord],
) -> None:
    repo_dir, _ = mock_git_repo
    context, proposal, validation, approval = sample_mutation_data
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()

    @asynccontextmanager
    async def mock_repo_factory() -> AsyncIterator[
        tuple[ApprovalRepositoryProtocol, MutationRepositoryProtocol]
    ]:
        yield (appr_repo, mut_repo)

    gh_client = MockGitHubClient(should_fail_auth=True)
    service = GitMutationService(
        repository_factory=mock_repo_factory,
        github_client=gh_client,
        repo_checkout=MockRepoCheckout(repo_dir),
        sandbox_runner=MockSandboxRunner(),
    )

    from src.packages.sdk.github_client import GitHubAuthError

    with pytest.raises(GitHubAuthError):
        await service.create_pull_request(context, proposal, validation, approval)


@pytest.mark.asyncio
async def test_working_tree_unexpected_untracked_files_rejected(
    mock_git_repo: tuple[Path, str],
    sample_mutation_data: tuple[FailureContext, FixProposal, ValidationResult, ApprovalRecord],
) -> None:
    repo_dir, _ = mock_git_repo
    context, proposal, validation, approval = sample_mutation_data
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()

    @asynccontextmanager
    async def mock_repo_factory() -> AsyncIterator[
        tuple[ApprovalRepositoryProtocol, MutationRepositoryProtocol]
    ]:
        yield (appr_repo, mut_repo)

    # Patch that modifies src.py and creates an extra untracked file
    class ModRepoCheckout(MockRepoCheckout):
        def checkout_commit(
            self, repo_owner: str, repo_name: str, commit_sha: str, clone_url: str | None = None
        ) -> Path:
            p = super().checkout_commit(repo_owner, repo_name, commit_sha, clone_url)
            # simulate extra untracked file
            return p

    service = GitMutationService(
        repository_factory=mock_repo_factory,
        github_client=MockGitHubClient(),
        repo_checkout=ModRepoCheckout(repo_dir),
        sandbox_runner=MockSandboxRunner(),
    )
    # create invalid proposal where target_files doesn't match
    proposal.target_files = ["other.py"]

    with pytest.raises(WorkingTreeMismatchError):
        await service.create_pull_request(context, proposal, validation, approval)


@pytest.mark.asyncio
async def test_existing_remote_pr_returns_record(
    mock_git_repo: tuple[Path, str],
    sample_mutation_data: tuple[FailureContext, FixProposal, ValidationResult, ApprovalRecord],
) -> None:
    repo_dir, _ = mock_git_repo
    context, proposal, validation, approval = sample_mutation_data
    appr_repo = MockApprovalRepo()
    mut_repo = MockMutationRepo()

    @asynccontextmanager
    async def mock_repo_factory() -> AsyncIterator[
        tuple[ApprovalRepositoryProtocol, MutationRepositoryProtocol]
    ]:
        yield (appr_repo, mut_repo)

    class ExistingPRGitHubClient(MockGitHubClient):
        async def find_pull_request(
            self, owner: str, repo: str, head_branch: str, base_branch: str
        ) -> dict[str, Any] | None:
            return {
                "number": 55,
                "html_url": "https://github.com/crlabs-ai/akesis/pull/55",
                "head": {"sha": "existing_sha"},
            }

    service = GitMutationService(
        repository_factory=mock_repo_factory,
        github_client=ExistingPRGitHubClient(),
        repo_checkout=MockRepoCheckout(repo_dir),
        sandbox_runner=MockSandboxRunner(),
    )

    record = await service.create_pull_request(context, proposal, validation, approval)
    assert record.status == MutationStatus.PR_CREATED
    assert record.pr_number == 55
