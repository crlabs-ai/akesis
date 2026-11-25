from contextlib import AbstractAsyncContextManager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.packages.shared.config import settings
from src.packages.shared.context_resolver import (
    CodebaseContextResolver,
    is_path_safe_and_within_root,
    normalize_repo_relative_path,
)
from src.packages.shared.models import (
    FailureCategory,
    FailureContext,
    FailureSignal,
    TracebackFrame,
    WorkflowRunConclusion,
)
from src.packages.shared.source_snapshot import SnapshotService, VerifiedSnapshot
from tests.support.fake_github import FakeGitHub, FakeInstallationAuth

APPROVED = "a" * 40


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    repo_dir = tmp_path / "mock_repo"
    repo_dir.mkdir()

    src_dir = repo_dir / "src"
    src_dir.mkdir()
    (src_dir / "calculator.py").write_text(
        "\n".join([f"def line_{i}(): pass" for i in range(1, 51)]),
        encoding="utf-8",
    )
    (src_dir / "helper.py").write_text("def helper(): pass\n", encoding="utf-8")
    (src_dir / "empty.py").write_text("", encoding="utf-8")
    (src_dir / "huge.py").write_text("a = 1\n" * 10000, encoding="utf-8")
    return repo_dir


def test_normalize_repo_relative_path() -> None:
    raw = "/home/runner/work/akesis/akesis/src/apps/api/main.py"
    clean = normalize_repo_relative_path(raw, repo_name="akesis")
    assert clean == "src/apps/api/main.py"

    win_path = "src\\packages\\shared\\utils.py"
    assert normalize_repo_relative_path(win_path) == "src/packages/shared/utils.py"


def test_path_traversal_rejection(temp_repo: Path) -> None:
    assert is_path_safe_and_within_root(temp_repo, "../../etc/passwd") is None
    assert is_path_safe_and_within_root(temp_repo, "/etc/passwd") is None
    assert is_path_safe_and_within_root(temp_repo, "src/../outside") is None
    assert is_path_safe_and_within_root(temp_repo, "src/calculator.py") is not None


def test_bounded_context_extraction(temp_repo: Path) -> None:
    resolver = CodebaseContextResolver(max_window_lines=10)
    context = FailureContext(
        incident_id="inc_001",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=1,
        workflow_name="CI",
        commit_sha="a" * 40,
        branch="main",
        run_url="http://ci",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=FailureCategory.TEST,
            error_type="ZeroDivisionError",
            message="err",
            target_file="src/calculator.py",
            target_line=25,
            extracted_snippet="err",
        ),
        raw_log_excerpt="err",
    )

    package = resolver._extract(context, temp_repo)
    assert package.retrieval_status == "success"
    assert len(package.code_evidences) == 1
    ev = package.code_evidences[0]
    assert ev.path == "src/calculator.py"
    assert ev.start_line == 20
    assert ev.end_line == 30
    assert ev.target_line == 25
    assert "line_25" in ev.content


def test_multiple_files_and_traceback_frames(temp_repo: Path) -> None:
    resolver = CodebaseContextResolver(max_evidence_files=2)
    context = FailureContext(
        incident_id="inc_multi",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=1,
        workflow_name="CI",
        commit_sha="a" * 40,
        branch="main",
        run_url="http://ci",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=FailureCategory.TEST,
            error_type="Error",
            message="err",
            target_file="src/calculator.py",
            target_line=10,
            extracted_snippet="err",
            traceback_frames=[
                TracebackFrame(file_path="src/helper.py", line_number=1, function_name="helper"),
                TracebackFrame(file_path="src/calculator.py", line_number=10),
            ],
        ),
        raw_log_excerpt="err",
    )

    package = resolver._extract(context, temp_repo)
    assert package.retrieval_status == "success"
    assert len(package.code_evidences) == 2


def test_oversized_and_empty_files_skipped(temp_repo: Path) -> None:
    resolver = CodebaseContextResolver(max_file_size_bytes=100)  # Very small limit
    context = FailureContext(
        incident_id="inc_skip",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=1,
        workflow_name="CI",
        commit_sha="a" * 40,
        branch="main",
        run_url="http://ci",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=FailureCategory.TEST,
            error_type="Error",
            message="err",
            target_file="src/huge.py",
            target_line=5,
            extracted_snippet="err",
            traceback_frames=[
                TracebackFrame(file_path="src/empty.py", line_number=1),
            ],
        ),
        raw_log_excerpt="err",
    )

    package = resolver._extract(context, temp_repo)
    assert package.retrieval_status == "empty"
    assert any("exceeds limit" in note for note in package.retrieval_notes)
    assert any("empty file" in note for note in package.retrieval_notes)


def _token(**overrides: Any) -> Any:
    values = {
        "incident_id": "inc_ctx",
        "pipeline_id": "pipe_inc_ctx",
        "delivery_id": "delivery-ctx",
        "event_repository_id": 1001,
        "source_repository_id": 1001,
        "owner": "crlabs-ai",
        "name": "akesis",
        "installation_id": 42,
        "head_sha": APPROVED,
        "head_branch": "main",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _context() -> FailureContext:
    return FailureContext(
        incident_id="inc_ctx",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=1,
        workflow_name="CI",
        commit_sha=APPROVED,
        branch="main",
        run_url="http://ci",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=FailureCategory.TEST,
            error_type="ImportError",
            message="err",
            target_file="src/app.py",
            target_line=2,
            extracted_snippet="err",
        ),
        raw_log_excerpt="err",
    )


class _Preparer:
    """Real snapshot engine over the synthetic Git Data API; `as_token` forces other work."""

    def __init__(self, fake: FakeGitHub, as_token: Any = None) -> None:
        self._service = SnapshotService(auth=FakeInstallationAuth(), client_factory=fake.client)
        self._as_token = as_token

    def prepare(self, token: object) -> AbstractAsyncContextManager[VerifiedSnapshot]:
        return self._service._open_unchecked(self._as_token or token)


def _fake(**overrides: Any) -> FakeGitHub:
    files = {
        "src/app.py": ("100644", b"from src.helper import helper\nraise ImportError()\n"),
        "src/helper.py": ("100644", b"def helper():\n    return 1\n"),
    }
    options: dict[str, Any] = {
        "repo_id": 1001,
        "full_name": "crlabs-ai/akesis",
        "files": files,
        "commit_sha": APPROVED,
    }
    options.update(overrides)
    return FakeGitHub(**options)


@pytest.fixture
def snapshot_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "snapshots"
    monkeypatch.setattr(settings, "snapshot_base_dir", str(root))
    return root


@pytest.mark.asyncio
async def test_evidence_is_read_from_a_verified_snapshot_and_removed(snapshot_root: Path) -> None:
    fake = _fake()
    resolver = CodebaseContextResolver(snapshot_service=_Preparer(fake))
    package = await resolver._resolve_context_unchecked(_context(), _token())

    assert package.retrieval_status == "success"
    assert [e.path for e in package.code_evidences] == ["src/app.py", "src/helper.py"]
    assert "raise ImportError()" in package.code_evidences[0].content
    assert any("verified snapshot" in note for note in package.retrieval_notes)
    # Only Git Data API reads by repository ID; the snapshot and workspace are gone afterwards.
    assert all(path.startswith("/repositories/1001") for path in fake.requests)
    assert not any(snapshot_root.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["symlink", "missing_commit", "repo_id_mismatch"])
async def test_rejected_snapshot_yields_unavailable_evidence(
    fault: str, snapshot_root: Path
) -> None:
    if fault == "symlink":
        fake = _fake(files={"src/app.py": ("120000", b"/etc/passwd")})
    else:
        fake = _fake(**{fault: True})
    resolver = CodebaseContextResolver(snapshot_service=_Preparer(fake))
    package = await resolver._resolve_context_unchecked(_context(), _token())

    assert package.retrieval_status == "unavailable"
    assert package.code_evidences == []
    assert package.retrieval_notes[0].startswith("Source snapshot unavailable (")
    assert not snapshot_root.exists() or not any(snapshot_root.iterdir())


@pytest.mark.asyncio
async def test_snapshot_of_other_work_is_never_read(snapshot_root: Path) -> None:
    preparer = _Preparer(_fake(), as_token=_token(incident_id="inc_other"))
    resolver = CodebaseContextResolver(snapshot_service=preparer)
    package = await resolver._resolve_context_unchecked(_context(), _token())

    assert package.retrieval_status == "unavailable"
    assert package.retrieval_notes == ["Source snapshot unavailable (binding)"]
    assert not any(snapshot_root.iterdir())


def test_resolver_has_no_checkout_or_personal_access_token_path() -> None:
    from src.packages.shared import context_resolver

    source = Path(context_resolver.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "repo_checkout",
        "checkout_commit",
        "github_token",
        "clone_from",
        "import git",
    ):
        assert forbidden not in source, forbidden
