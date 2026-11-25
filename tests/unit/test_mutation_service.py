"""Phase 1.7 mutation: verified snapshot -> Git Data API -> branch -> PR in the event repository.

The service is driven through its private body with explicit fixtures, as ADR-0010 requires while
the hold is active. Source content comes from the real snapshot engine over the synthetic GitHub API
in tests/support/fake_github.py; nothing here contacts GitHub or creates a real pull request.
"""

import subprocess
import tempfile
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import src.packages.shared.mutation_service as mutation_module
from src.packages.database.repositories import (
    ApprovalRepositoryProtocol,
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
    MutationRecord,
    MutationStatus,
    RepositoryIdentity,
    ValidationResult,
    ValidationStatus,
    WorkflowRunConclusion,
    WorkflowRunProvenance,
)
from src.packages.shared.mutation_service import (
    MUTATION_TOKEN_PERMISSIONS,
    BranchConflictError,
    ForkSourceRefusedError,
    GitMutationService,
    GitPushError,
    MutationError,
    PreflightCheckError,
    PrePushValidationError,
    PullRequestError,
    StaleCommitError,
    UnsafeBranchError,
    WorkingTreeMismatchError,
)
from src.packages.shared.persisted_provenance import ProvenanceRejected
from src.packages.shared.source_snapshot import SnapshotService, VerifiedSnapshot
from tests.support.fake_github import SYNTHETIC_TOKEN, FakeGitHub, FakeInstallationAuth

REPO_ID = 1001
OWNER, NAME = "crlabs-ai", "akesis"
FULL = f"{OWNER}/{NAME}"
APPROVED = "a" * 40
MOVED = "b" * 40
FILES = {
    "src.py": ("100644", b"x = 1\n"),
    "pkg/util.py": ("100644", b"def f():\n    return 1\n"),
    "README.md": ("100644", b"# demo\n"),
}
PATCH = "--- a/src.py\n+++ b/src.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n"
BRANCH = "akesis/fix/inc_mut_01/prop_mut_01"


# ---- fixtures ---------------------------------------------------------------------------------


def _token(**overrides: Any) -> Any:
    values = {
        "incident_id": "inc_mut_01",
        "pipeline_id": "pipe_inc_mut_01",
        "delivery_id": "delivery-mut-1",
        "event_repository_id": REPO_ID,
        "source_repository_id": REPO_ID,
        "owner": OWNER,
        "name": NAME,
        "installation_id": 42,
        "head_sha": APPROVED,
        "head_branch": "main",
        "run_id": 1,
        "run_attempt": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _provenance(
    *, source_id: int = REPO_ID, source_name: str = FULL, branch: str = "main"
) -> WorkflowRunProvenance:
    return WorkflowRunProvenance(
        delivery_id="delivery-mut-1",
        event_repository=RepositoryIdentity(id=REPO_ID, full_name=FULL),
        source_repository=RepositoryIdentity(id=source_id, full_name=source_name),
        head_sha=APPROVED,
        head_branch=branch,
        run_id=1,
        run_attempt=1,
        installation_id=42,
        verified_at=datetime.now(UTC),
    )


def _work(
    branch: str = "main", provenance: WorkflowRunProvenance | None = None
) -> tuple[FailureContext, FixProposal, ValidationResult, ApprovalRecord]:
    context = FailureContext(
        incident_id="inc_mut_01",
        provenance=provenance or _provenance(branch=branch),
        repository_owner=OWNER,
        repository_name=NAME,
        run_id=1,
        workflow_name="CI",
        commit_sha=APPROVED,
        branch=branch,
        run_url="https://github.com/crlabs-ai/akesis/actions/runs/1",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(category=FailureCategory.TEST, message="failed", target_file="src.py"),
        raw_log_excerpt="err",
    )
    proposal = FixProposal(
        proposal_id="prop_mut_01",
        incident_id="inc_mut_01",
        commit_sha=APPROVED,
        status="proposed",
        is_valid=True,
        unified_diff=PATCH,
        target_files=["src.py"],
        rationale="Fix x",
        risk_level="low",
        confidence_score=0.95,
    )
    validation = ValidationResult(
        validation_id="val_01",
        proposal_id="prop_mut_01",
        incident_id="inc_mut_01",
        commit_sha=APPROVED,
        status=ValidationStatus.PASSED,
        command_executed="pytest",
        exit_code=0,
        duration_ms=20.0,
    )
    approval = ApprovalRecord(
        approval_id="appr_01",
        incident_id="inc_mut_01",
        proposal_id="prop_mut_01",
        commit_sha=APPROVED,
        status=ApprovalStatus.APPROVED,
        decided_by="lead_dev",
        decided_at=datetime.now(UTC),
    )
    return context, proposal, validation, approval


class MockApprovalRepo(ApprovalRepositoryProtocol):
    def __init__(self) -> None:
        self.records: dict[str, ApprovalRecord] = {}

    async def create_approval(self, record: ApprovalRecord) -> ApprovalRecord:
        self.records[record.approval_id] = record
        return record

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        return self.records.get(approval_id)

    async def get_by_proposal_id(self, proposal_id: str) -> ApprovalRecord | None:
        return next((r for r in self.records.values() if r.proposal_id == proposal_id), None)

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
        self.history: list[MutationStatus] = []

    async def create_mutation(self, record: MutationRecord) -> MutationRecord:
        self.records[record.mutation_id] = record
        self.history.append(record.status)
        return record

    async def get_mutation(self, mutation_id: str) -> MutationRecord | None:
        return self.records.get(mutation_id)

    async def get_by_proposal_and_commit(
        self, proposal_id: str, commit_sha: str
    ) -> MutationRecord | None:
        matches = [
            r
            for r in self.records.values()
            if r.proposal_id == proposal_id and r.base_commit_sha == commit_sha
        ]
        assert len(matches) <= 1, "duplicate mutation rows for one proposal and commit"
        return matches[0] if matches else None

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
            self.history.append(status)
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


class _ControlledValidation:
    """Explicit test double for the gated pre-push validation step."""

    def __init__(
        self, status: ValidationStatus = ValidationStatus.PASSED, exit_code: int = 0
    ) -> None:
        self.status = status
        self.exit_code = exit_code
        self.calls = 0

    async def validate_fix(
        self, proposal: FixProposal, context: FailureContext
    ) -> ValidationResult:
        self.calls += 1
        return ValidationResult(
            validation_id=f"val_pre_push_{proposal.proposal_id}",
            proposal_id=proposal.proposal_id,
            incident_id=context.incident_id,
            commit_sha=proposal.commit_sha,
            status=self.status,
            command_executed="pytest",
            exit_code=self.exit_code,
            duration_ms=1.0,
        )


class _SnapshotPreparer:
    """Real snapshot engine over the synthetic API. `as_token` forces a snapshot of other work."""

    def __init__(self, fake: FakeGitHub, as_token: Any = None) -> None:
        self._service = SnapshotService(auth=FakeInstallationAuth(), client_factory=fake.client)
        self._as_token = as_token

    def prepare(self, token: object) -> AbstractAsyncContextManager[VerifiedSnapshot]:
        return self._service._open_unchecked(self._as_token or token)


class _Untouchable:
    """Any use is a test failure: proves that a refusal happened before this collaborator."""

    def __init__(self, label: str) -> None:
        self._label = label

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"{self._label} touched ({name}) before refusal")

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"{self._label} called before refusal")


@dataclass
class Harness:
    fake: FakeGitHub
    service: GitMutationService
    auth: FakeInstallationAuth
    approvals: MockApprovalRepo
    mutations: MockMutationRepo
    validation: _ControlledValidation
    work: tuple[FailureContext, FixProposal, ValidationResult, ApprovalRecord]
    token: Any = field(default_factory=_token)

    async def run(self) -> MutationRecord:
        return await self.service._create_pull_request_unchecked(*self.work, self.token)

    def writes(self, method: str, prefix: str) -> list[Any]:
        return [
            body
            for verb, path, body in self.fake.writes
            if verb == method and path.startswith(prefix)
        ]


def _harness(
    *,
    fake: FakeGitHub | None = None,
    branch: str = "main",
    validation: _ControlledValidation | None = None,
    preparer_token: Any = None,
    token: Any = None,
) -> Harness:
    fake = fake or FakeGitHub(
        repo_id=REPO_ID, full_name=FULL, files=dict(FILES), commit_sha=APPROVED
    )
    auth = FakeInstallationAuth()
    approvals, mutations = MockApprovalRepo(), MockMutationRepo()
    work = _work(branch)
    approvals.records[work[3].approval_id] = work[3]

    @asynccontextmanager
    async def factory() -> AsyncIterator[
        tuple[ApprovalRepositoryProtocol, MutationRepositoryProtocol]
    ]:
        yield approvals, mutations

    service = GitMutationService(
        repository_factory=factory,
        auth=auth,
        client_factory=fake.client,
        snapshot_service=_SnapshotPreparer(fake, preparer_token),
    )
    controlled = validation or _ControlledValidation()
    service.validation_service = controlled  # type: ignore[assignment]
    return Harness(
        fake,
        service,
        auth,
        approvals,
        mutations,
        controlled,
        work,
        token or _token(head_branch=branch),
    )


def _untouchable_service() -> GitMutationService:
    service = GitMutationService(
        repository_factory=_Untouchable("database"),
        auth=_Untouchable("token issuer"),
        client_factory=_Untouchable("GitHub client"),
        snapshot_service=_Untouchable("snapshot service"),
    )
    service.validation_service = _Untouchable("validation")  # type: ignore[assignment]
    return service


@pytest.fixture(autouse=True)
def snapshot_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "snapshots"
    monkeypatch.setattr(settings, "snapshot_base_dir", str(root))
    return root


# ---- 1-4: refusal before any I/O, identity binding -------------------------------------------


@pytest.mark.asyncio
async def test_fork_incident_is_refused_before_any_io(snapshot_root: Path) -> None:
    context, proposal, validation, approval = _work(
        provenance=_provenance(source_id=2002, source_name="attacker/akesis")
    )
    token = _token(source_repository_id=2002)
    with pytest.raises(ForkSourceRefusedError):
        await _untouchable_service()._create_pull_request_unchecked(
            context, proposal, validation, approval, token
        )
    assert not snapshot_root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token_overrides", "provenance"),
    [
        ({"source_repository_id": 1002}, None),  # token says cross-repository
        ({}, "source_id"),  # persisted provenance says cross-repository under the same name
    ],
)
async def test_event_and_source_repository_id_mismatch_is_refused(
    token_overrides: dict[str, Any], provenance: str | None, snapshot_root: Path
) -> None:
    prov = _provenance(source_id=1002) if provenance else None
    context, proposal, validation, approval = _work(provenance=prov)
    with pytest.raises(ForkSourceRefusedError):
        await _untouchable_service()._create_pull_request_unchecked(
            context, proposal, validation, approval, _token(**token_overrides)
        )
    assert not snapshot_root.exists()


@pytest.mark.asyncio
async def test_missing_provenance_token_is_refused_before_any_io() -> None:
    with pytest.raises(PreflightCheckError):
        await _untouchable_service()._create_pull_request_unchecked(*_work(), None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"head_sha": "c" * 40},
        {"head_branch": "other"},
        {"incident_id": "inc_other"},
        {"owner": "someone-else"},
        {"event_repository_id": 2002, "source_repository_id": 2002},
    ],
)
async def test_token_not_describing_the_incident_is_refused_before_io(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(PreflightCheckError):
        await _untouchable_service()._create_pull_request_unchecked(*_work(), _token(**overrides))


@pytest.mark.asyncio
async def test_wrong_approved_commit_sha_is_refused_before_io() -> None:
    context, proposal, validation, approval = _work()
    approval.commit_sha = "c" * 40
    with pytest.raises(ProvenanceRejected):
        await _untouchable_service()._create_pull_request_unchecked(
            context, proposal, validation, approval, _token()
        )


@pytest.mark.asyncio
async def test_snapshot_of_other_work_is_refused_before_any_write() -> None:
    harness = _harness(preparer_token=_token(incident_id="inc_other", pipeline_id="pipe_other"))
    with pytest.raises(PreflightCheckError):
        await harness.run()
    assert harness.fake.writes == []
    assert harness.auth.calls == []  # no mutation token was issued
    assert harness.validation.calls == 0


# ---- success path, targeting, credentials -----------------------------------------------------


@pytest.mark.asyncio
async def test_pull_request_opens_in_the_event_repository() -> None:
    harness = _harness()
    record = await harness.run()

    assert record.status == MutationStatus.PR_CREATED
    assert (record.repository_owner, record.repository_name) == (OWNER, NAME)
    assert record.pr_url == f"https://github.com/{FULL}/pull/1"
    pull = harness.fake.pulls[0]
    assert pull["head"]["ref"] == BRANCH
    assert pull["base"]["repo"]["id"] == pull["head"]["repo"]["id"] == REPO_ID
    assert harness.fake.refs[BRANCH] == record.resulting_commit_sha
    # The remediation commit carries exactly the validated change on the approved parent.
    tree, parents = harness.fake.commits[record.resulting_commit_sha or ""]
    assert parents == [APPROVED]
    assert (
        harness.fake._flatten(tree)["src.py"][1]
        != harness.fake._flatten(harness.fake.root_tree_sha)["src.py"][1]
    )
    assert (
        harness.fake._flatten(tree)["README.md"]
        == harness.fake._flatten(harness.fake.root_tree_sha)["README.md"]
    )
    assert MutationStatus.PUSHED in harness.mutations.history


@pytest.mark.asyncio
async def test_installation_token_is_scoped_to_the_event_repository() -> None:
    harness = _harness()
    await harness.run()
    mutation_scope = harness.auth.scopes[-1]
    assert harness.auth.calls == [42]
    assert mutation_scope == {
        "repository_ids": [REPO_ID],
        "permissions": {"contents": "write", "pull_requests": "write", "metadata": "read"},
    }
    assert MUTATION_TOKEN_PERMISSIONS == mutation_scope["permissions"]


@pytest.mark.asyncio
async def test_pat_is_never_used() -> None:
    assert not hasattr(settings, "github_token")  # Phase 1.8: the PAT setting no longer exists
    harness = _harness()
    await harness.run()
    assert harness.fake.authorizations
    assert set(harness.fake.authorizations) == {f"Bearer {SYNTHETIC_TOKEN}"}
    source = Path(mutation_module.__file__).read_text(encoding="utf-8")
    for forbidden in ("github_token", "GitHubClient", "repo_checkout", "clone_from", "import git"):
        assert forbidden not in source, forbidden


@pytest.mark.asyncio
async def test_no_clone_and_no_shared_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    import git

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("mutation must not clone or check out a repository")

    monkeypatch.setattr(git.Repo, "clone_from", refuse)
    # Phase 1.8 removed the shared-checkout manager entirely.
    assert importlib.util.find_spec("src.packages.sdk.repo_checkout") is None
    record = await _harness().run()
    assert record.status == MutationStatus.PR_CREATED


@pytest.mark.asyncio
async def test_no_credential_reaches_files_arguments_git_config_or_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    seen_args: list[str] = []
    real_run = subprocess.run

    def recording_run(args: Any, *rest: Any, **kwargs: Any) -> Any:
        seen_args.extend(str(item) for item in args)
        seen_args.extend(str(value) for value in (kwargs.get("env") or {}).values())
        return real_run(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording_run)
    await _harness().run()

    assert seen_args, "the exact patch is applied with git apply"
    assert not any(SYNTHETIC_TOKEN in item for item in seen_args)
    for path in tmp_path.rglob("*"):
        assert path.name != ".git", f"unexpected Git metadata at {path}"
        if path.is_file():
            assert SYNTHETIC_TOKEN.encode() not in path.read_bytes(), path
    captured = capsys.readouterr()
    assert SYNTHETIC_TOKEN not in captured.out + captured.err


def _feature_fake(**overrides: Any) -> FakeGitHub:
    """`feature/x` holds the approved commit; `main` is elsewhere and has never merged it."""
    options: dict[str, Any] = {
        "repo_id": REPO_ID,
        "full_name": FULL,
        "files": dict(FILES),
        "commit_sha": APPROVED,
        "refs": {"main": "d" * 40, "feature/x": APPROVED},
    }
    options.update(overrides)
    return FakeGitHub(**options)


@pytest.mark.asyncio
async def test_feature_branch_not_merged_into_main_gets_pr_against_that_branch() -> None:
    fake = _feature_fake()
    harness = _harness(fake=fake, branch="feature/x")
    record = await harness.run()

    pull = fake.pulls[0]
    assert pull["base"]["ref"] == "feature/x"
    assert pull["head"]["ref"] == BRANCH
    assert pull["base"]["repo"]["id"] == pull["head"]["repo"]["id"] == REPO_ID
    assert "**Base (failing source) branch:** `feature/x`" in pull["body"]
    # The PR holds only the approved change: one commit on the approved parent, one file changed.
    tree, parents = fake.commits[record.resulting_commit_sha or ""]
    assert parents == [APPROVED] == [fake.refs["feature/x"]]
    before, after = fake._flatten(fake.root_tree_sha), fake._flatten(tree)
    assert {path for path in after if after[path] != before.get(path)} == {"src.py"}
    assert set(after) == set(before)
    # The default branch is never read for targeting and never written.
    assert fake.refs["main"] == "d" * 40
    assert not any("/heads/main" in path for _, path, _ in fake.writes)


@pytest.mark.asyncio
async def test_default_branch_failure_gets_pr_against_the_default_branch() -> None:
    harness = _harness()
    await harness.run()
    assert harness.fake.pulls[0]["base"]["ref"] == "main"
    assert (
        harness.fake.refs["main"] == APPROVED
    )  # base untouched; only the remediation branch is new


@pytest.mark.asyncio
async def test_source_branch_freshness_is_checked_before_objects_branch_and_pr() -> None:
    harness = _harness(fake=_feature_fake(), branch="feature/x")
    await harness.run()
    order = [
        "fresh" if path == "/git/ref/heads/feature/x" else path
        for verb, path, _ in harness.fake.writes
        if path == "/git/ref/heads/feature/x" or verb == "POST"
    ]
    assert order[0] == "fresh"  # before any remote object
    assert order[order.index("/git/refs") - 1] == "fresh"  # immediately before the branch
    assert order[order.index("/pulls") - 1] == "fresh"  # immediately before the pull request


# ---- freshness: changed, missing or inaccessible source branch --------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["main", "feature/x"])
async def test_changed_source_branch_aborts_before_any_remote_object(branch: str) -> None:
    fake = _feature_fake()
    fake.refs[branch] = MOVED
    harness = _harness(fake=fake, branch=branch)
    with pytest.raises(StaleCommitError):
        await harness.run()
    assert [verb for verb, _, _ in fake.writes if verb == "POST"] == []
    assert fake.pulls == []
    record = next(iter(harness.mutations.records.values()))
    assert record.status == MutationStatus.FAILED


@pytest.mark.asyncio
async def test_source_branch_moving_during_mutation_aborts_before_branch_creation() -> None:
    fake = _feature_fake(move_refs_on_commit={"feature/x": MOVED})
    harness = _harness(fake=fake, branch="feature/x")
    with pytest.raises(StaleCommitError):
        await harness.run()
    assert harness.writes("POST", "/git/refs") == []
    assert BRANCH not in fake.refs
    assert fake.pulls == []


@pytest.mark.asyncio
async def test_source_branch_moving_after_branch_creation_aborts_before_pr() -> None:
    fake = _feature_fake(move_refs_on_branch={"feature/x": MOVED})
    harness = _harness(fake=fake, branch="feature/x")
    with pytest.raises(StaleCommitError):
        await harness.run()
    assert harness.writes("POST", "/pulls") == []
    assert fake.pulls == []
    assert fake.refs["feature/x"] == MOVED  # never rebased or reset by Akesis
    assert fake.refs["main"] == "d" * 40  # and no other branch is substituted as the base


@pytest.mark.asyncio
async def test_missing_source_branch_aborts_without_substitution() -> None:
    fake = _feature_fake()
    fake.refs.pop("feature/x")
    harness = _harness(fake=fake, branch="feature/x")
    with pytest.raises(StaleCommitError) as error:
        await harness.run()
    assert "no longer exists" in str(error.value)
    assert [verb for verb, _, _ in fake.writes if verb == "POST"] == []
    assert fake.pulls == []


@pytest.mark.asyncio
async def test_inaccessible_source_branch_aborts() -> None:
    fake = _feature_fake(fail={"ref_read": 403})
    harness = _harness(fake=fake, branch="feature/x")
    with pytest.raises(GitPushError) as error:
        await harness.run()
    assert "freshness check (forbidden)" in str(error.value)
    assert [verb for verb, _, _ in fake.writes if verb == "POST"] == []


@pytest.mark.asyncio
async def test_feature_branch_fork_incident_is_still_refused_before_any_io(
    snapshot_root: Path,
) -> None:
    context, proposal, validation, approval = _work(
        branch="feature/x",
        provenance=_provenance(source_id=2002, source_name="attacker/akesis", branch="feature/x"),
    )
    token = _token(source_repository_id=2002, head_branch="feature/x")
    with pytest.raises(ForkSourceRefusedError):
        await _untouchable_service()._create_pull_request_unchecked(
            context, proposal, validation, approval, token
        )
    assert not snapshot_root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["fork", "archived"])
async def test_fork_or_archived_target_repository_is_refused(flag: str) -> None:
    fake = FakeGitHub(repo_id=REPO_ID, full_name=FULL, files=dict(FILES), commit_sha=APPROVED)
    setattr(fake, flag, True)
    with pytest.raises(MutationError):
        await _harness(fake=fake).run()
    assert [verb for verb, _, _ in fake.writes if verb == "POST"] == []


# ---- idempotency, branches and retries --------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_mutation_is_idempotent() -> None:
    harness = _harness()
    first = await harness.run()
    writes_after_first = len(harness.fake.writes)
    second = await harness.run()
    assert second.mutation_id == first.mutation_id
    assert second.pr_number == first.pr_number
    assert len(harness.fake.writes) == writes_after_first  # no GitHub call at all
    assert len(harness.fake.pulls) == 1
    assert len(harness.mutations.records) == 1


@pytest.mark.asyncio
async def test_persisted_pr_created_record_is_returned_without_github() -> None:
    harness = _harness()
    context, proposal, _, approval = harness.work
    existing = MutationRecord(
        mutation_id="mut_existing_01",
        proposal_id=proposal.proposal_id,
        approval_id=approval.approval_id,
        incident_id=context.incident_id,
        repository_owner=OWNER,
        repository_name=NAME,
        base_commit_sha=APPROVED,
        branch_name=BRANCH,
        status=MutationStatus.PR_CREATED,
        pr_number=99,
        pr_url=f"https://github.com/{FULL}/pull/99",
    )
    harness.mutations.records[existing.mutation_id] = existing
    result = await harness.run()
    assert result.mutation_id == "mut_existing_01"
    assert harness.fake.requests == []


@pytest.mark.asyncio
async def test_existing_unrelated_remediation_branch_is_never_overwritten() -> None:
    fake = FakeGitHub(repo_id=REPO_ID, full_name=FULL, files=dict(FILES), commit_sha=APPROVED)
    fake.refs[BRANCH] = APPROVED  # exists, but does not hold the validated remediation tree
    harness = _harness(fake=fake)
    with pytest.raises(BranchConflictError):
        await harness.run()
    assert fake.refs[BRANCH] == APPROVED
    assert harness.writes("POST", "/git/refs") == []
    assert fake.pulls == []
    assert all(verb in ("GET", "POST") for verb, _, _ in fake.writes)  # never PATCH or force


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["blob", "tree", "commit", "ref"])
async def test_git_data_api_failure_stops_before_pull_request(operation: str) -> None:
    fake = FakeGitHub(
        repo_id=REPO_ID,
        full_name=FULL,
        files=dict(FILES),
        commit_sha=APPROVED,
        fail={operation: 500},
    )
    harness = _harness(fake=fake)
    with pytest.raises(GitPushError) as error:
        await harness.run()
    assert "github_error" in str(error.value)
    assert BRANCH not in fake.refs
    assert fake.pulls == []
    assert next(iter(harness.mutations.records.values())).status == MutationStatus.FAILED


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["tamper_created_blob", "tamper_created_tree"])
async def test_created_object_that_does_not_verify_stops_the_mutation(tamper: str) -> None:
    fake = FakeGitHub(repo_id=REPO_ID, full_name=FULL, files=dict(FILES), commit_sha=APPROVED)
    setattr(fake, tamper, True)
    with pytest.raises(GitPushError) as error:
        await _harness(fake=fake).run()
    assert "integrity" in str(error.value)
    assert BRANCH not in fake.refs


@pytest.mark.asyncio
async def test_pr_failure_after_branch_creation_then_safe_retry() -> None:
    fake = FakeGitHub(
        repo_id=REPO_ID,
        full_name=FULL,
        files=dict(FILES),
        commit_sha=APPROVED,
        fail={"pull_create": 502},
    )
    harness = _harness(fake=fake)
    with pytest.raises(PullRequestError):
        await harness.run()
    failed = next(iter(harness.mutations.records.values()))
    assert failed.status == MutationStatus.FAILED
    assert fake.refs[BRANCH] == failed.resulting_commit_sha
    branch_head = fake.refs[BRANCH]

    fake.fail.clear()
    commits_before = len(harness.writes("POST", "/git/commits"))
    retried = await harness.run()

    assert retried.mutation_id == failed.mutation_id
    assert retried.status == MutationStatus.PR_CREATED
    assert len(harness.mutations.records) == 1
    assert len(harness.writes("POST", "/git/refs")) == 1  # the branch was created exactly once
    assert len(harness.writes("POST", "/git/commits")) == commits_before  # reused, not rebuilt
    assert fake.refs[BRANCH] == branch_head
    assert len(fake.pulls) == 1


@pytest.mark.asyncio
async def test_existing_matching_open_pr_is_reused_not_duplicated() -> None:
    harness = _harness()
    await harness.run()
    # Simulate a crash after PR creation but before the database recorded it.
    record = next(iter(harness.mutations.records.values()))
    record.status = MutationStatus.PUSHED
    retried = await harness.run()
    assert retried.pr_number == 1
    assert len(harness.fake.pulls) == 1
    assert len(harness.writes("POST", "/pulls")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate_pull",
    [
        lambda pull: pull.update(state="closed"),
        lambda pull: pull["head"].update(sha="f" * 40),
        lambda pull: pull["head"]["repo"].update(id=999),
    ],
)
async def test_unrelated_or_closed_existing_pr_is_not_reused(mutate_pull: Any) -> None:
    harness = _harness()
    await harness.run()
    mutate_pull(harness.fake.pulls[0])
    next(iter(harness.mutations.records.values())).status = MutationStatus.PUSHED
    with pytest.raises(PullRequestError):
        await harness.run()
    assert len(harness.fake.pulls) == 1


@pytest.mark.asyncio
async def test_no_automatic_merge() -> None:
    harness = _harness()
    await harness.run()
    for verb, path, body in harness.fake.writes:
        assert "merge" not in path.lower()
        assert verb in ("GET", "POST")
        assert "auto_merge" not in (body or {})
    assert all("graphql" not in path for path in harness.fake.requests)
    pull = harness.fake.pulls[0]
    assert pull["state"] == "open"
    assert "Akesis never merges it" in pull["body"]


@pytest.mark.asyncio
async def test_pr_body_states_only_recorded_validation() -> None:
    harness = _harness()
    await harness.run()
    body = harness.fake.pulls[0]["body"]
    assert "**Validation before approval:** `PASSED` (exit code `0`, command `pytest`)" in body
    assert "**Revalidation before push:** `PASSED`" in body
    assert "lead_engineer" not in body and "recently" not in body


# ---- preserved preflight, scope and failure behaviour -----------------------------------------


@pytest.mark.asyncio
async def test_unapproved_proposal_rejected() -> None:
    harness = _harness()
    harness.work[3].status = ApprovalStatus.PENDING
    with pytest.raises(PreflightCheckError) as exc:
        await harness.run()
    assert "must be 'approved'" in str(exc.value)
    assert harness.fake.requests == []


@pytest.mark.asyncio
async def test_mismatched_proposal_id_rejected() -> None:
    harness = _harness()
    harness.work[3].proposal_id = "other_proposal_id"
    with pytest.raises(PreflightCheckError):
        await harness.run()


@pytest.mark.asyncio
async def test_unpersisted_approval_rejected() -> None:
    harness = _harness()
    harness.approvals.records.clear()
    with pytest.raises(PreflightCheckError):
        await harness.run()
    assert harness.fake.requests == []


@pytest.mark.asyncio
async def test_unsafe_branch_name_rejected() -> None:
    service = GitMutationService()
    with pytest.raises(UnsafeBranchError):
        service.validate_branch_name("akesis/fix/../../root")
    with pytest.raises(UnsafeBranchError):
        service.validate_branch_name("main")


@pytest.mark.asyncio
async def test_pre_push_validation_failure_aborts_before_any_write() -> None:
    harness = _harness(validation=_ControlledValidation(ValidationStatus.FAILED, 1))
    with pytest.raises(PrePushValidationError):
        await harness.run()
    assert harness.fake.writes == []
    assert harness.auth.calls == []


@pytest.mark.asyncio
async def test_changes_outside_target_scope_rejected() -> None:
    harness = _harness()
    harness.work[1].target_files = ["other.py"]
    with pytest.raises(WorkingTreeMismatchError):
        await harness.run()
    assert harness.fake.writes == []


@pytest.mark.asyncio
async def test_patch_apply_failure_aborts() -> None:
    harness = _harness()
    harness.work[
        1
    ].unified_diff = "--- a/src.py\n+++ b/src.py\n@@ -100,1 +100,1 @@\n-x = 999\n+x = 1000\n"
    with pytest.raises(MutationError):
        await harness.run()
    assert harness.fake.writes == []


@pytest.mark.asyncio
async def test_patch_creating_a_symlink_is_refused() -> None:
    harness = _harness()
    harness.work[1].unified_diff = (
        "diff --git a/leak b/leak\nnew file mode 120000\n--- /dev/null\n+++ b/leak\n"
        "@@ -0,0 +1 @@\n+/etc/passwd\n\\ No newline at end of file\n"
    )
    harness.work[1].target_files = ["leak"]
    with pytest.raises(WorkingTreeMismatchError):
        await harness.run()
    assert harness.fake.writes == []


@pytest.mark.asyncio
async def test_file_deletion_is_committed_as_a_tree_deletion() -> None:
    harness = _harness()
    harness.work[1].unified_diff = (
        "diff --git a/README.md b/README.md\ndeleted file mode 100644\n--- a/README.md\n"
        "+++ /dev/null\n@@ -1 +0,0 @@\n-# demo\n"
    )
    harness.work[1].target_files = ["README.md"]
    record = await harness.run()
    tree, _ = harness.fake.commits[record.resulting_commit_sha or ""]
    assert "README.md" not in harness.fake._flatten(tree)
    assert harness.writes("POST", "/git/blobs") == []


@pytest.mark.asyncio
async def test_installation_authentication_failure_is_reported_without_writes() -> None:
    from src.packages.sdk.github_app_auth import GitHubAppAuthenticationError

    harness = _harness()

    async def failing(*args: Any, **kwargs: Any) -> Any:
        raise GitHubAppAuthenticationError("denied", code="github_app_http_error")

    harness.auth.installation_token = failing  # type: ignore[method-assign]
    with pytest.raises(MutationError) as error:
        await harness.run()
    assert "github_app_http_error" in str(error.value)
    assert harness.fake.writes == []


@pytest.mark.asyncio
async def test_permission_denied_on_pull_request_is_reported() -> None:
    fake = FakeGitHub(
        repo_id=REPO_ID,
        full_name=FULL,
        files=dict(FILES),
        commit_sha=APPROVED,
        fail={"pull_create": 403},
    )
    with pytest.raises(PullRequestError) as error:
        await _harness(fake=fake).run()
    assert "forbidden" in str(error.value)
