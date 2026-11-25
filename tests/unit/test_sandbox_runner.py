"""Sandbox runner contract: snapshot-only sources, exact patching, and mount policy (ADR-0011).

Docker is never started here. The container invocation is captured and inspected.
"""

import asyncio
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.packages.sdk import sandbox_runner
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
    # Read-only root; the only other writable path is one bounded tmpfs (Phase 1.13).
    assert "--read-only" in options
    tmpfs = [options[index + 1] for index, token in enumerate(options) if token == "--tmpfs"]
    assert tmpfs == ["/tmp:rw,exec,nosuid,nodev,size=64m,mode=1777"]
    for flag, value in (
        ("--security-opt", "no-new-privileges"),
        ("--user", f"{os.getuid()}:{os.getgid()}"),  # the workspace owner, never fixed
        ("--pids-limit", "256"),
        ("--memory", "512m"),
    ):
        assert options[options.index(flag) + 1] == value
    assert "--cpus=1.0" in options and "--rm" in options
    assert not any("docker.sock" in token or token == "--privileged" for token in argv)
    assert "--label" not in options  # only the executor labels containers (Phase 1.14a)


def test_truncate_output() -> None:
    runner = DockerSandboxRunner(max_output_chars=50)
    short_text = "short log"
    assert runner._truncate_output(short_text) == short_text

    long_text = "a" * 100
    truncated = runner._truncate_output(long_text)
    assert len(truncated) <= 100
    assert "[... output truncated ...]" in truncated


# --- container cleanup (no Docker: subprocess is faked) --------------------------------------


class _Log:
    def __init__(self) -> None:
        self.errors: list[tuple[str, dict[str, Any]]] = []

    def error(self, event: str, **kw: Any) -> None:
        self.errors.append((event, kw))

    def info(self, *a: Any, **kw: Any) -> None: ...

    def warning(self, *a: Any, **kw: Any) -> None: ...


def _fake_docker(monkeypatch: pytest.MonkeyPatch, inspect: Any) -> list[list[str]]:
    """Fake `subprocess.run` for rm/inspect. `inspect(attempt)` returns (returncode, stderr)."""
    calls: list[list[str]] = []

    def run(argv: list[str], **_: Any) -> Any:
        calls.append(list(argv))
        if argv[:2] == ["docker", "rm"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        code, err = inspect(sum(1 for c in calls if c[1] == "container"))
        return SimpleNamespace(returncode=code, stdout="", stderr=err)

    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr("time.sleep", lambda _: None)
    return calls


def test_cleanup_is_confirmed_only_by_docker_reporting_no_such_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_docker(monkeypatch, lambda n: (0, "") if n == 1 else (1, "No such object: x"))
    assert sandbox_runner._remove_container("akesis-val-0123456789abcdef") is True
    # Exactly this container, by its exact reference, every time: never a filter or a prune.
    assert {c[-1] for c in calls} == {"akesis-val-0123456789abcdef"}
    assert [c[:3] for c in calls] == [
        ["docker", "rm", "-f"],
        ["docker", "container", "inspect"],
    ] * 2


@pytest.mark.parametrize(
    "inspect",
    [
        lambda n: (0, ""),  # the container is still there
        lambda n: (1, "Cannot connect to the Docker daemon"),  # not proof of absence
    ],
)
def test_cleanup_failure_is_bounded_logged_and_never_raises(
    monkeypatch: pytest.MonkeyPatch, inspect: Any
) -> None:
    log = _Log()
    monkeypatch.setattr(sandbox_runner, "logger", log)
    calls = _fake_docker(monkeypatch, inspect)
    assert sandbox_runner._remove_container("abc123") is False
    assert len(calls) == 2 * sandbox_runner._REMOVE_ATTEMPTS
    assert log.errors == [("sandbox_container_cleanup_failed", {"container": "abc123"})]


def test_cleanup_survives_a_missing_docker_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*_: Any, **__: Any) -> Any:
        raise FileNotFoundError("docker")

    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(sandbox_runner, "logger", _Log())
    assert sandbox_runner._remove_container("abc123") is False


@pytest.mark.asyncio
async def test_a_timeout_after_create_removes_the_created_id_even_if_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container_id = "f" * 64
    removed: list[str] = []

    class _Proc:
        def __init__(self, argv: tuple[str, ...]) -> None:
            self.argv, self.killed = argv, False
            self.returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            if self.argv[1] == "create":
                self.returncode = 0
                return container_id.encode() + b"\n", b""
            await asyncio.sleep(60)  # `docker start --attach` never finishes
            return b"", b""

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            return -9

    procs: list[_Proc] = []

    async def fake_exec(*argv: str, **_: object) -> _Proc:
        procs.append(_Proc(argv))
        return procs[-1]

    def failing_remove(ref: str) -> bool:
        removed.append(ref)
        return False  # cleanup could not be confirmed

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(sandbox_runner, "_remove_container", failing_remove)
    with pytest.raises(sandbox_runner.SandboxTimeoutError):
        await DockerSandboxRunner()._execute_container(tmp_path, ["pytest", "-v"], timeout=0.2)
    assert [p.argv[1] for p in procs] == ["create", "start"]
    assert procs[1].argv[-1] == container_id and procs[1].killed  # client stopped first
    assert removed == [container_id]  # the exact ID `docker create` returned, nothing else


@pytest.mark.asyncio
async def test_a_failed_create_reports_exit_125_like_docker_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Proc:
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b"Error response from daemon: No such image"

    async def fake_exec(*argv: str, **_: object) -> _Proc:
        assert argv[1] == "create"  # nothing is started after a failed create
        return _Proc()

    removed: list[str] = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(sandbox_runner, "_remove_container", lambda ref: removed.append(ref))
    result = await DockerSandboxRunner()._execute_container(tmp_path, ["pytest", "-v"], 30)
    assert result.exit_code == 125 and "No such image" in result.stderr
    assert len(removed) == 1 and removed[0].startswith("akesis-val-")  # its own name only


# --- container user: the workspace owner, never root (no Docker needed) ----------------------


def test_the_container_user_is_the_workspace_owner(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir(mode=0o700)
    assert sandbox_runner._workspace_owner(workspace) == (os.getuid(), os.getgid())


def _fake_owner(monkeypatch: pytest.MonkeyPatch, *, euid: int, uid: int, gid: int) -> None:
    monkeypatch.setattr("os.geteuid", lambda: euid)
    monkeypatch.setattr(
        "os.lstat", lambda _: SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=uid, st_gid=gid)
    )


@pytest.mark.parametrize(
    "euid, uid, gid",
    [
        (0, 0, 0),  # the sandbox process itself is root
        (0, 1000, 1000),  # root process, even with a non-root workspace
        (1000, 0, 1000),  # a root-owned workspace
        (1000, 1000, 0),  # the root group
    ],
)
def test_root_is_refused(monkeypatch: pytest.MonkeyPatch, euid: int, uid: int, gid: int) -> None:
    _fake_owner(monkeypatch, euid=euid, uid=uid, gid=gid)
    with pytest.raises(sandbox_runner.SandboxError, match="as root"):
        sandbox_runner._workspace_owner(Path("/w"))


def test_a_workspace_owned_by_another_account_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_owner(monkeypatch, euid=1001, uid=1000, gid=1000)
    with pytest.raises(sandbox_runner.SandboxError, match="not owned"):
        sandbox_runner._workspace_owner(Path("/w"))


def test_a_symlinked_workspace_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    (tmp_path / "link").symlink_to(real)
    with pytest.raises(sandbox_runner.SandboxError, match="not a directory"):
        sandbox_runner._workspace_owner(tmp_path / "link")


@pytest.mark.asyncio
async def test_root_is_refused_before_any_docker_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[Any] = []

    async def record(*argv: str, **_: object) -> Any:
        spawned.append(argv)
        raise AssertionError("no Docker subprocess may start")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record)
    monkeypatch.setattr("os.geteuid", lambda: 0)
    with pytest.raises(sandbox_runner.SandboxError, match="as root"):
        await DockerSandboxRunner()._execute_container(tmp_path, ["pytest", "-v"], timeout=30)
    assert spawned == []
