"""Sandbox runner contract: snapshot-only sources, exact patching, and mount policy (ADR-0011).

Docker is never started here. The container invocation is captured and inspected.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.packages.sdk.sandbox_runner import (
    DockerSandboxRunner,
    PatchApplicationError,
    SandboxWorkspaceError,
)
from src.packages.shared.config import settings
from src.packages.shared.models import ValidationCommand
from src.packages.shared.persisted_provenance import ProvenanceRejected
from src.packages.shared.source_snapshot import SnapshotService, create_workspace, discard_workspace
from tests.support.fake_github import FakeGitHub, FakeInstallationAuth

PATCH = (
    "--- a/calc.py\n"
    "+++ b/calc.py\n"
    "@@ -1,2 +1,3 @@\n"
    " def add(a, b):\n"
    "+    # comment\n"
    "     return a + b\n"
)


@pytest.fixture
def snapshot_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "snaps"
    monkeypatch.setattr(settings, "snapshot_base_dir", str(root))
    return root


def _token() -> Any:
    return SimpleNamespace(
        incident_id="inc_sbx",
        pipeline_id="pipe_inc_sbx",
        delivery_id="d-sbx",
        event_repository_id=1001,
        source_repository_id=1001,
        owner="crlabs-ai",
        name="akesis",
        installation_id=42,
        head_sha="a" * 40,
        head_branch="main",
    )


@pytest.mark.asyncio
async def test_exact_patch_applies_inside_the_writable_workspace(snapshot_root: Path) -> None:
    fake = FakeGitHub(
        repo_id=1001,
        full_name="crlabs-ai/akesis",
        files={"calc.py": ("100644", b"def add(a, b):\n    return a + b\n")},
        commit_sha="a" * 40,
    )
    service = SnapshotService(auth=FakeInstallationAuth(), client_factory=fake.client)
    async with service._open_unchecked(_token()) as snapshot:
        workspace = create_workspace(snapshot)
        try:
            runner = DockerSandboxRunner()
            runner._apply_patch_exact(workspace, PATCH)
            assert "# comment" in (workspace / "calc.py").read_text(encoding="utf-8")
            assert not (workspace / ".git").exists()
            assert not list(workspace.glob("*.patch"))
            assert not list(workspace.parent.glob("*.patch"))
        finally:
            discard_workspace(workspace)


@pytest.mark.asyncio
async def test_rejected_patch_raises_and_leaves_no_residue(snapshot_root: Path) -> None:
    fake = FakeGitHub(
        repo_id=1001,
        full_name="crlabs-ai/akesis",
        files={"calc.py": ("100644", b"def add(a, b): return a + b\n")},
        commit_sha="a" * 40,
    )
    service = SnapshotService(auth=FakeInstallationAuth(), client_factory=fake.client)
    async with service._open_unchecked(_token()) as snapshot:
        workspace = create_workspace(snapshot)
        try:
            bad = "--- a/calc.py\n+++ b/calc.py\n@@ -10,5 +10,5 @@\n nonexistent_line\n"
            with pytest.raises(PatchApplicationError):
                DockerSandboxRunner()._apply_patch_exact(workspace, bad)
            assert not list(workspace.parent.glob("*.patch"))
        finally:
            discard_workspace(workspace)


@pytest.mark.asyncio
async def test_arbitrary_directory_is_never_accepted_as_a_source(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text("attacker-controlled\n")
    runner = DockerSandboxRunner()
    with pytest.raises(SandboxWorkspaceError):
        await runner._run_validation_unchecked(tmp_path, "", ValidationCommand.PYTEST)


@pytest.mark.asyncio
async def test_public_entry_rejects_missing_token_and_non_snapshot(tmp_path: Path) -> None:
    runner = DockerSandboxRunner()
    with pytest.raises(ProvenanceRejected) as no_token:
        await runner.run_validation(tmp_path, "", ValidationCommand.PYTEST)
    assert no_token.value.code == "missing"
    with pytest.raises(ProvenanceRejected) as no_snapshot:
        await runner.run_validation(tmp_path, "", ValidationCommand.PYTEST, provenance=None)
    assert no_snapshot.value.code == "missing"


@pytest.mark.asyncio
async def test_docker_mounts_only_the_writable_workspace(
    snapshot_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeGitHub(
        repo_id=1001,
        full_name="crlabs-ai/akesis",
        files={"calc.py": ("100644", b"x = 1\n")},
        commit_sha="a" * 40,
    )
    service = SnapshotService(auth=FakeInstallationAuth(), client_factory=fake.client)
    captured: list[tuple[str, ...]] = []

    class _Proc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def fake_exec(*argv: str, **_: object) -> _Proc:
        captured.append(argv)
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    async with service._open_unchecked(_token()) as snapshot:
        workspace = create_workspace(snapshot)
        try:
            await DockerSandboxRunner()._execute_container(workspace, ["pytest", "-v"], timeout=30)
            base_text = str(next(snapshot_root.glob(f"{snapshot.snapshot_id}/source")))
        finally:
            discard_workspace(workspace)
    argv = captured[0]
    options = argv[: argv.index("pytest")]  # docker options only; the command also has -v
    mounts = [options[index + 1] for index, token in enumerate(options) if token == "-v"]
    assert mounts == [f"{workspace.resolve()}:/workspace:rw"]
    assert ".git" not in " ".join(argv)
    assert base_text not in " ".join(argv)  # the immutable base is never mounted
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "--cap-drop" in argv and argv[argv.index("--cap-drop") + 1] == "ALL"


def test_truncate_output() -> None:
    runner = DockerSandboxRunner(max_output_chars=50)
    short_text = "short log"
    assert runner._truncate_output(short_text) == short_text

    long_text = "a" * 100
    truncated = runner._truncate_output(long_text)
    assert len(truncated) <= 100
    assert "[... output truncated ...]" in truncated
