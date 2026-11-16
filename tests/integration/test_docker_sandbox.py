"""Docker-dependent sandbox tests (marker `docker`), run against a real daemon (ADR-0011).

Each test prepares a real snapshot from the synthetic GitHub API and runs real containers. Skipped
when the daemon is unavailable.
"""

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from src.packages.sdk.sandbox_runner import DockerSandboxRunner, SandboxTimeoutError
from src.packages.shared.config import settings
from src.packages.shared.models import ValidationCommand
from src.packages.shared.source_snapshot import (
    SnapshotService,
    create_workspace,
    discard_workspace,
)
from tests.support.fake_github import FakeGitHub, FakeInstallationAuth

IMAGE = "python:3.12-slim"
PATCH = (
    "--- a/main.py\n"
    "+++ b/main.py\n"
    "@@ -1,2 +1,3 @@\n"
    " def compute():\n"
    "+    # validated in docker\n"
    "     return 42\n"
)


def is_docker_functional() -> bool:
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, timeout=3.0, check=False)
        return res.returncode == 0
    except Exception:
        return False


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not is_docker_functional(), reason="Docker daemon is not available"),
]


@pytest.fixture
def snapshot_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "snapshots"
    monkeypatch.setattr(settings, "snapshot_base_dir", str(root))
    return root


def _token() -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        incident_id="inc_docker",
        pipeline_id="pipe_inc_docker",
        delivery_id="d-docker",
        event_repository_id=1001,
        source_repository_id=1001,
        owner="crlabs-ai",
        name="akesis",
        installation_id=42,
        head_sha="a" * 40,
        head_branch="main",
    )


def _service() -> SnapshotService:
    fake = FakeGitHub(
        repo_id=1001,
        full_name="crlabs-ai/akesis",
        files={"main.py": ("100644", b"def compute():\n    return 42\n")},
        commit_sha="a" * 40,
    )
    return SnapshotService(auth=FakeInstallationAuth(), client_factory=fake.client)


def _running_sleeps() -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "--format", "{{.Command}}"], capture_output=True, text=True, check=False
    ).stdout
    return [line for line in out.splitlines() if "sleep 30" in line]


@pytest.mark.asyncio
async def test_real_container_validates_a_patched_snapshot_copy(snapshot_root: Path) -> None:
    async with _service()._open_unchecked(_token()) as snapshot:
        workspace = create_workspace(snapshot)
        try:
            runner = DockerSandboxRunner(image_name=IMAGE)
            runner._apply_patch_exact(workspace, PATCH)
            result = await runner._execute_container(
                workspace,
                ["python3", "-m", "compileall", "-q", "."],
                timeout=120,
            )
            assert result.exit_code == 0
            assert result.timed_out is False
        finally:
            discard_workspace(workspace)


@pytest.mark.asyncio
async def test_container_sees_no_git_metadata_and_not_the_immutable_base(
    snapshot_root: Path,
) -> None:
    async with _service()._open_unchecked(_token()) as snapshot:
        base = next(snapshot_root.glob(f"{snapshot.snapshot_id}/source"))
        workspace = create_workspace(snapshot)
        try:
            runner = DockerSandboxRunner(image_name=IMAGE)
            probe = f"test ! -e .git && test -f main.py && test ! -e '{base}' && echo isolated"
            result = await runner._execute_container(workspace, ["sh", "-c", probe], timeout=120)
            assert result.exit_code == 0, result.stderr
            assert "isolated" in result.stdout
        finally:
            discard_workspace(workspace)


@pytest.mark.asyncio
async def test_timeout_force_removes_the_container(snapshot_root: Path) -> None:
    async with _service()._open_unchecked(_token()) as snapshot:
        workspace = create_workspace(snapshot)
        try:
            runner = DockerSandboxRunner(image_name=IMAGE)
            with pytest.raises(SandboxTimeoutError):
                await runner._execute_container(workspace, ["sleep", "30"], timeout=2)
            await asyncio.sleep(1)
            assert _running_sleeps() == []
        finally:
            discard_workspace(workspace)


@pytest.mark.asyncio
async def test_cancellation_force_removes_the_container(snapshot_root: Path) -> None:
    async with _service()._open_unchecked(_token()) as snapshot:
        workspace = create_workspace(snapshot)
        try:
            runner = DockerSandboxRunner(image_name=IMAGE)
            task = asyncio.create_task(
                runner._execute_container(workspace, ["sleep", "30"], timeout=60)
            )
            await asyncio.sleep(3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(1)
            assert _running_sleeps() == []
        finally:
            discard_workspace(workspace)


@pytest.mark.asyncio
async def test_docker_failure_still_removes_the_workspace(snapshot_root: Path) -> None:
    async with _service()._open_unchecked(_token()) as snapshot:
        runner = DockerSandboxRunner(image_name="akesis-does-not-exist:v0")
        result = await runner._run_validation_unchecked(
            snapshot, "", ValidationCommand.PYTHON_SYNTAX
        )
        assert result.exit_code != 0
        assert not list(snapshot_root.glob(f"{snapshot.snapshot_id}/work/*"))
