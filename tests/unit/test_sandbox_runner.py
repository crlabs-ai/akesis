from pathlib import Path

import pytest

from src.packages.sdk.sandbox_runner import (
    DockerSandboxRunner,
    PatchApplicationError,
)


def test_truncate_output() -> None:
    runner = DockerSandboxRunner(max_output_chars=50)
    short_text = "short log"
    assert runner._truncate_output(short_text) == short_text

    long_text = "a" * 100
    truncated = runner._truncate_output(long_text)
    assert len(truncated) <= 100
    assert "[... output truncated ...]" in truncated


def test_materialize_workspace_and_exact_patch_apply(tmp_path: Path) -> None:
    source_repo = tmp_path / "src_repo"
    source_repo.mkdir()
    (source_repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    dest_workspace = tmp_path / "dest_workspace"
    dest_workspace.mkdir()

    runner = DockerSandboxRunner()
    runner._materialize_workspace(source_repo, dest_workspace)

    assert (dest_workspace / "calc.py").exists()

    # Valid exact patch
    patch = (
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def add(a, b):\n"
        "+    # comment\n"
        "     return a + b\n"
    )
    runner._apply_patch_exact(dest_workspace, patch)
    content = (dest_workspace / "calc.py").read_text(encoding="utf-8")
    assert "# comment" in content


def test_patch_apply_failure_raises_patch_error(tmp_path: Path) -> None:
    source_repo = tmp_path / "src_repo2"
    source_repo.mkdir()
    (source_repo / "calc.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")

    dest_workspace = tmp_path / "dest_workspace2"
    dest_workspace.mkdir()

    runner = DockerSandboxRunner()
    runner._materialize_workspace(source_repo, dest_workspace)

    # Mismatched corrupted patch
    bad_patch = "--- a/calc.py\n+++ b/calc.py\n@@ -10,5 +10,5 @@\n non_existent_line_123\n"
    with pytest.raises(PatchApplicationError):
        runner._apply_patch_exact(dest_workspace, bad_patch)
