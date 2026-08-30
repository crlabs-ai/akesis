from pathlib import Path

import pytest

from src.packages.sdk.repo_checkout import (
    RepoCheckoutProtocol,
    RepositoryCheckoutError,
)
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


class FailingCheckoutManager(RepoCheckoutProtocol):
    def checkout_commit(
        self,
        repo_owner: str,
        repo_name: str,
        commit_sha: str,
        clone_url: str | None = None,
    ) -> Path:
        raise RepositoryCheckoutError("Remote git clone timed out")


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

    package = resolver.resolve_context(context, repo_root=temp_repo)
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

    package = resolver.resolve_context(context, repo_root=temp_repo)
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

    package = resolver.resolve_context(context, repo_root=temp_repo)
    assert package.retrieval_status == "empty"
    assert any("exceeds limit" in note for note in package.retrieval_notes)
    assert any("empty file" in note for note in package.retrieval_notes)


def test_checkout_failure_handled_gracefully() -> None:
    resolver = CodebaseContextResolver(checkout_manager=FailingCheckoutManager())
    context = FailureContext(
        incident_id="inc_fail",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=1,
        workflow_name="CI",
        commit_sha="b" * 40,
        branch="main",
        run_url="http://ci",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=FailureCategory.TEST,
            error_type="Error",
            message="err",
            target_file="src/test.py",
            target_line=1,
            extracted_snippet="err",
        ),
        raw_log_excerpt="err",
    )

    package = resolver.resolve_context(context)
    assert package.retrieval_status == "unavailable"
    assert any("Repository checkout failed" in note for note in package.retrieval_notes)
