"""Docker-dependent sandbox tests (marker `docker`), run against a real daemon (ADR-0011).

Each test prepares a real snapshot from the synthetic GitHub API and runs real containers. Skipped
when the daemon is unavailable.
"""

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from src.packages.sdk.sandbox_runner import (
    COMMAND_INVOCATION_MAP,
    DockerSandboxRunner,
    SandboxTimeoutError,
)
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
        assert result.exit_code == 125  # a failed create reports what `docker run` did
        assert "akesis-does-not-exist" in result.stderr
        assert not list(snapshot_root.glob(f"{snapshot.snapshot_id}/work/*"))
        assert _sandbox_containers() == []


# Container-creation race: a timeout or cancellation while Docker is still creating the container.
# The daemon finishes an in-flight create even when its client is killed, so a cleanup that ran
# before the create landed used to leave a `Created` container behind (3/3 at 0.05 s).


def _sandbox_containers() -> list[str]:
    """Every sandbox container in any state, `Created` included."""
    out = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "name=akesis-val-",
            "--format",
            "{{.Names}} {{.Status}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return out.splitlines()


async def _still_none_after_settling() -> list[str]:
    """Look again after a pause, long enough for a late create to have landed."""
    await asyncio.sleep(3)
    return _sandbox_containers()


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0.01, 0.05, 0.1, 0.3])
async def test_a_timeout_during_creation_leaves_no_container(
    snapshot_root: Path, timeout: float
) -> None:
    assert _sandbox_containers() == []
    async with _service()._open_unchecked(_token()) as snapshot:
        workspace = create_workspace(snapshot)
        try:
            for _ in range(3):
                with pytest.raises(SandboxTimeoutError):
                    await DockerSandboxRunner(image_name=IMAGE)._execute_container(
                        workspace, ["sleep", "30"], timeout=timeout
                    )
                assert _sandbox_containers() == []  # settled before the error was raised
            assert await _still_none_after_settling() == []
        finally:
            discard_workspace(workspace)


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", [0.0, 0.02, 0.05, 0.2])
async def test_a_cancellation_during_creation_leaves_no_container(
    snapshot_root: Path, delay: float
) -> None:
    assert _sandbox_containers() == []
    async with _service()._open_unchecked(_token()) as snapshot:
        workspace = create_workspace(snapshot)
        try:
            for _ in range(3):
                task = asyncio.create_task(
                    DockerSandboxRunner(image_name=IMAGE)._execute_container(
                        workspace, ["sleep", "30"], timeout=60
                    )
                )
                await asyncio.sleep(delay)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert _sandbox_containers() == []
            assert await _still_none_after_settling() == []
        finally:
            discard_workspace(workspace)


@pytest.mark.asyncio
async def test_a_second_cancellation_cannot_cut_cleanup_short(snapshot_root: Path) -> None:
    async with _service()._open_unchecked(_token()) as snapshot:
        workspace = create_workspace(snapshot)
        try:
            for delay in (0.02, 0.05, 2.0):
                task = asyncio.create_task(
                    DockerSandboxRunner(image_name=IMAGE)._execute_container(
                        workspace, ["sleep", "30"], timeout=60
                    )
                )
                await asyncio.sleep(delay)
                task.cancel()
                await asyncio.sleep(0.01)  # cleanup has begun
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert _sandbox_containers() == []
            assert await _still_none_after_settling() == []
        finally:
            discard_workspace(workspace)


@pytest.mark.asyncio
async def test_a_completed_run_keeps_exit_code_and_streams_and_leaves_no_container(
    snapshot_root: Path,
) -> None:
    result = await _run(snapshot_root, "echo out; echo err >&2; exit 3")
    assert (result.exit_code, result.stdout, result.stderr, result.timed_out) == (
        3,
        "out\n",
        "err\n",
        False,
    )
    for _ in range(50):  # --rm removes an exited container asynchronously
        if _sandbox_containers() == []:
            break
        await asyncio.sleep(0.1)
    assert _sandbox_containers() == []


@pytest.mark.asyncio
async def test_one_jobs_cleanup_never_removes_another_jobs_container(snapshot_root: Path) -> None:
    async with _service()._open_unchecked(_token()) as snapshot:
        survivor_ws, victim_ws = create_workspace(snapshot), create_workspace(snapshot)
        try:
            survivor = asyncio.create_task(
                DockerSandboxRunner(image_name=IMAGE)._execute_container(
                    survivor_ws, ["sh", "-c", "sleep 6; echo survived"], timeout=60
                )
            )
            await asyncio.sleep(2)  # the survivor's container is running
            assert len(_sandbox_containers()) == 1
            for timeout in (0.01, 0.05, 0.3):
                with pytest.raises(SandboxTimeoutError):
                    await DockerSandboxRunner(image_name=IMAGE)._execute_container(
                        victim_ws, ["sleep", "30"], timeout=timeout
                    )
            cancelled = asyncio.create_task(
                DockerSandboxRunner(image_name=IMAGE)._execute_container(
                    victim_ws, ["sleep", "30"], timeout=60
                )
            )
            await asyncio.sleep(0.05)
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            assert len(_sandbox_containers()) == 1  # only the survivor, still there
            result = await survivor
            assert result.exit_code == 0 and result.stdout == "survived\n", result.stderr
        finally:
            discard_workspace(survivor_ws)
            discard_workspace(victim_ws)


# Phase 1.13: read-only root filesystem and the bounded /tmp tmpfs.


async def _run(snapshot_root: Path, script: str, *, image: str = IMAGE, **runner: Any) -> Any:
    async with _service()._open_unchecked(_token()) as snapshot:
        workspace = create_workspace(snapshot)
        try:
            return await DockerSandboxRunner(image_name=image, **runner)._execute_container(
                workspace, ["sh", "-c", script], timeout=120
            )
        finally:
            discard_workspace(workspace)


@pytest.mark.asyncio
async def test_root_is_read_only_and_only_tmp_and_workspace_are_writable(
    snapshot_root: Path,
) -> None:
    script = (
        "for p in /probe /etc/probe /home/akesis/probe /usr/local/probe /var/tmp/probe; do "
        "touch $p 2>/dev/null && echo WROTE:$p; done; "
        "touch /tmp/probe && echo tmp-ok; touch /workspace/probe && echo ws-ok; "
        "grep -E '^overlay / ' /proc/mounts | cut -d' ' -f4 | cut -d, -f1; "
        "grep ' /tmp ' /proc/mounts; "
        'python3 -c "import tempfile; print(\\"tempdir=\\" + tempfile.gettempdir())"'
    )
    result = await _run(snapshot_root, script)
    assert result.exit_code == 0, result.stderr
    out = result.stdout
    assert "WROTE:" not in out
    assert "tmp-ok" in out and "ws-ok" in out
    assert "\nro\n" in f"\n{out}"  # root mount options start with ro
    tmp_line = next(line for line in out.splitlines() if " /tmp " in line)
    assert all(opt in tmp_line for opt in ("tmpfs", "rw", "nosuid", "nodev", "size=65536k"))
    assert "tempdir=/tmp" in out  # temp files go to the tmpfs, not the workspace


@pytest.mark.asyncio
async def test_tmpfs_is_bounded(snapshot_root: Path) -> None:
    result = await _run(
        snapshot_root, "dd if=/dev/zero of=/tmp/fill bs=1M count=100 2>&1; echo dd=$?"
    )
    assert "No space left on device" in result.stdout and "dd=0" not in result.stdout


@pytest.mark.asyncio
async def test_network_identity_and_capabilities_are_restricted(snapshot_root: Path) -> None:
    script = (
        "id -u; id -g; ls /sys/class/net; "
        "grep -E '^(CapEff|CapBnd|NoNewPrivs):' /proc/self/status; "
        'python3 -c "import socket; s=socket.socket(); s.settimeout(3); '
        "s.connect(('1.1.1.1', 443))\" 2>&1 | tail -1"
    )
    result = await _run(snapshot_root, script)
    lines = result.stdout.splitlines()
    assert lines[:2] == ["1000", "1000"]
    assert "lo" in lines and "eth0" not in lines
    status = {k: v.strip() for k, _, v in (line.partition(":") for line in lines if ":" in line)}
    assert status["CapEff"] == "0000000000000000" and status["CapBnd"] == "0000000000000000"
    assert status["NoNewPrivs"] == "1"
    assert "Network is unreachable" in result.stdout


@pytest.mark.asyncio
async def test_memory_exhaustion_is_killed(snapshot_root: Path) -> None:
    result = await _run(
        snapshot_root,
        'python3 -c "b = bytearray(1024 * 1024 * 1024); print(len(b))"',
        memory_limit="64m",
    )
    assert result.exit_code == 137  # OOM-killed by the memory cgroup


@pytest.mark.asyncio
async def test_pid_limit_caps_process_creation(snapshot_root: Path) -> None:
    script = (
        'python3 -c "\n'
        "import threading, time\n"
        "n = 0\n"
        "try:\n"
        "    while n < 1000:\n"
        "        threading.Thread(target=time.sleep, args=(30,), daemon=True).start()\n"
        "        n += 1\n"
        "except RuntimeError:\n"
        "    pass\n"
        "print(n, flush=True)\n"
        'import os; os._exit(0)"'
    )
    result = await _run(snapshot_root, script)
    assert result.exit_code == 0, result.stderr
    assert 0 < int(result.stdout.split()[-1]) < 256


@pytest.mark.asyncio
async def test_unexpected_error_after_start_removes_the_container(
    snapshot_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_communicate = asyncio.subprocess.Process.communicate
    calls: list[int] = []

    async def failing_communicate(self: Any, *args: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:  # `docker create` completes normally
            return await real_communicate(self, *args)
        await asyncio.sleep(3)  # `docker start`: the container is running by now
        raise RuntimeError("client failure")

    monkeypatch.setattr(asyncio.subprocess.Process, "communicate", failing_communicate)
    async with _service()._open_unchecked(_token()) as snapshot:
        workspace = create_workspace(snapshot)
        try:
            with pytest.raises(RuntimeError):
                await DockerSandboxRunner(image_name=IMAGE)._execute_container(
                    workspace, ["sleep", "30"], timeout=60
                )
            await asyncio.sleep(1)
            assert _running_sleeps() == []
        finally:
            discard_workspace(workspace)


@pytest.mark.asyncio
async def test_concurrent_validations_do_not_share_tmp_or_workspace(snapshot_root: Path) -> None:
    script = (
        "m=$(cat /proc/sys/kernel/random/uuid); echo $m > /tmp/mine; echo $m > /workspace/mine; "
        "sleep 3; echo tmp=$(ls /tmp | tr '\\n' ' '); echo ws=$(cat /workspace/mine); "
        "echo tmpm=$(cat /tmp/mine)"
    )
    first, second = await asyncio.gather(_run(snapshot_root, script), _run(snapshot_root, script))
    for result in (first, second):
        assert result.exit_code == 0, result.stderr
        values = dict(line.split("=", 1) for line in result.stdout.splitlines())
        assert values["tmp"].split() == ["mine"]
        assert values["ws"] == values["tmpm"]
    assert first.stdout != second.stdout


def _validator_image_present() -> bool:
    res = subprocess.run(
        ["docker", "image", "inspect", settings.sandbox_image], capture_output=True, check=False
    )
    return res.returncode == 0


PROJECT = {
    "conftest.py": b"",
    "pkg/__init__.py": b"",
    "pkg/calc.py": b"def add(a: int, b: int) -> int:\n    return a + b\n",
    "tests/test_calc.py": (
        b"import os\n"
        b"import subprocess\n"
        b"import sys\n"
        b"import tempfile\n"
        b"from pathlib import Path\n"
        b"\n"
        b"from pkg.calc import add\n"
        b"\n"
        b"\n"
        b"def test_add() -> None:\n"
        b"    assert add(1, 2) == 3\n"
        b"\n"
        b"\n"
        b"def test_tmp_path_is_on_the_tmpfs(tmp_path: Path) -> None:\n"
        b"    (tmp_path / 'f').write_text('x')\n"
        b"    assert str(tmp_path).startswith('/tmp/')\n"
        b"\n"
        b"\n"
        b"def test_tempfile_and_subprocess() -> None:\n"
        b"    with tempfile.NamedTemporaryFile() as handle:\n"
        b"        handle.write(b'x')\n"
        b"    res = subprocess.run([sys.executable, '-c', 'print(1)'], capture_output=True)\n"
        b"    assert res.returncode == 0\n"
        b"\n"
        b"\n"
        b"def test_root_stays_read_only_during_validation() -> None:\n"
        b"    for path in ('/probe', os.path.expanduser('~/probe'), '/usr/local/probe'):\n"
        b"        try:\n"
        b"            open(path, 'w').close()\n"
        b"        except OSError as error:\n"
        b"            assert error.errno == 30  # EROFS\n"
        b"        else:\n"
        b"            raise AssertionError(path)\n"
    ),
}


@pytest.mark.asyncio
@pytest.mark.skipif(
    not is_docker_functional() or not _validator_image_present(),
    reason="validator image not built: docker build -f docker/Dockerfile.validator "
    "-t akesis-validator:v1 docker",
)
@pytest.mark.parametrize("command", list(ValidationCommand))
async def test_validator_image_runs_every_command_read_only(
    snapshot_root: Path, command: ValidationCommand
) -> None:
    fake = FakeGitHub(
        repo_id=1001,
        full_name="crlabs-ai/akesis",
        files={path: ("100644", body) for path, body in PROJECT.items()},
        commit_sha="a" * 40,
    )
    service = SnapshotService(auth=FakeInstallationAuth(), client_factory=fake.client)
    async with service._open_unchecked(_token()) as snapshot:
        workspace = create_workspace(snapshot)
        try:
            result = await DockerSandboxRunner(
                image_name=settings.sandbox_image
            )._execute_container(workspace, COMMAND_INVOCATION_MAP[command], timeout=180)
            assert result.exit_code == 0, result.stdout + result.stderr
            assert not list(workspace.glob("pytest-of-*"))  # temp files stayed on the tmpfs
            if command is ValidationCommand.PYTEST:
                assert "4 passed" in result.stdout
        finally:
            discard_workspace(workspace)
