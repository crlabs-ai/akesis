"""Phase 1.14a against a real Docker daemon and the real `akesis-validator:v1` image.

Validation runs through the executor (in-process ASGI app with the real hardened runner) and is
compared with the local runner result for result. A real `python -m src.apps.executor` process on a
Unix socket proves the entrypoint, the credential guard, SO_PEERCRED and the signed hold rejection.
"""

import asyncio
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from src.apps.executor.app import EXECUTOR_LABEL, ExecutorConfig, create_app, startup_cleanup
from src.packages.sdk.remote_sandbox_runner import ExecutorRejectedError, RemoteSandboxRunner
from src.packages.sdk.sandbox_runner import DockerSandboxRunner, ValidationExecutionResult
from src.packages.shared.config import settings
from src.packages.shared.models import (
    FailureCategory,
    FailureContext,
    FailureSignal,
    FixProposal,
    ValidationCommand,
    ValidationResult,
    WorkflowRunConclusion,
)
from src.packages.shared.source_snapshot import SnapshotService
from src.packages.shared.validation_service import ValidationService
from tests.support.fake_github import FakeGitHub, FakeInstallationAuth

REPO = Path(__file__).resolve().parents[2]
VALIDATOR = "akesis-validator:v1"
REQ, RESP = "q" * 40, "r" * 40


def _docker_ok() -> bool:
    try:
        info = subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False)
        image = subprocess.run(
            ["docker", "image", "inspect", VALIDATOR], capture_output=True, timeout=5, check=False
        )
        return info.returncode == 0 and image.returncode == 0
    except Exception:
        return False


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        not _docker_ok(),
        reason="Docker or the validator image is unavailable: docker build -f "
        "docker/Dockerfile.validator -t akesis-validator:v1 docker",
    ),
]

CALC = b"def add(a: int, b: int) -> int:\n    return a + b\n"
PROJECT = {
    "conftest.py": b"",
    "pkg/__init__.py": b"",
    "pkg/calc.py": CALC,
    "tests/__init__.py": b"",
    "tests/test_calc.py": (
        b"from pkg.calc import add\n\n\ndef test_add() -> None:\n    assert add(1, 2) == 3\n"
    ),
    # The sandbox restrictions, checked from inside a validation that runs through the executor.
    "tests/test_sandbox.py": b"""import os
import socket
import tempfile
from pathlib import Path


def _status(field: str) -> str:
    with open("/proc/self/status") as handle:
        for line in handle:
            if line.startswith(field + ":"):
                return line.split(":", 1)[1].strip()
    return ""


def test_root_is_read_only() -> None:
    for path in ("/probe", os.path.expanduser("~/probe"), "/usr/local/probe"):
        try:
            open(path, "w").close()
        except OSError as error:
            assert error.errno == 30
        else:
            raise AssertionError(path)


def test_tmp_is_the_writable_tmpfs(tmp_path: Path) -> None:
    assert str(tmp_path).startswith("/tmp/") and tempfile.gettempdir() == "/tmp"


def test_no_network() -> None:
    assert os.listdir("/sys/class/net") == ["lo"]
    probe = socket.socket()
    probe.settimeout(3)
    try:
        probe.connect(("1.1.1.1", 443))
    except OSError:
        return
    finally:
        probe.close()
    raise AssertionError("network reachable")


def test_identity_and_capabilities() -> None:
    uid, gid = os.getuid(), os.getgid()
    assert uid != 0 and gid != 0  # never root
    workspace = os.stat(".")
    assert (workspace.st_uid, workspace.st_gid) == (uid, gid)  # the workspace owner, any host UID
    assert _status("CapEff") == "0000000000000000"
    assert _status("CapBnd") == "0000000000000000"
    assert _status("NoNewPrivs") == "1"
""",
}
SLOW = {
    **PROJECT,
    "tests/test_slow.py": b"import time\n\n\ndef test_slow() -> None:\n    time.sleep(60)\n",
}
PATCH = (
    "--- a/pkg/calc.py\n+++ b/pkg/calc.py\n@@ -1,2 +1,3 @@\n"
    " def add(a: int, b: int) -> int:\n+    # validated through the executor\n     return a + b\n"
)
BAD_PATCH = "--- a/pkg/calc.py\n+++ b/pkg/calc.py\n@@ -1,2 +1,2 @@\n def nope():\n-    x\n+    y\n"


@pytest.fixture(autouse=True)
def snapshot_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "snapshots"
    monkeypatch.setattr(settings, "snapshot_base_dir", str(root))
    return root


@pytest.fixture(autouse=True)
def no_executor_containers_left() -> Iterator[None]:
    yield
    left = _labelled()
    assert left == [], f"executor containers left behind: {left}"


def _labelled() -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label={EXECUTOR_LABEL}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return out.split()


def _token() -> Any:
    return SimpleNamespace(
        incident_id="inc_exec",
        pipeline_id="pipe_inc_exec",
        delivery_id="d-exec",
        event_repository_id=1001,
        source_repository_id=1001,
        owner="crlabs-ai",
        name="akesis",
        installation_id=42,
        head_sha="a" * 40,
        head_branch="main",
    )


class _Snapshots:
    """A real verified snapshot of `files` from the synthetic GitHub API."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    @asynccontextmanager
    async def prepare(self, token: object) -> AsyncIterator[Any]:
        fake = FakeGitHub(
            repo_id=1001,
            full_name="crlabs-ai/akesis",
            files={path: ("100644", body) for path, body in self.files.items()},
            commit_sha="a" * 40,
        )
        service = SnapshotService(auth=FakeInstallationAuth(), client_factory=fake.client)
        async with service._open_unchecked(_token()) as snapshot:
            yield snapshot


class _Unchecked:
    """Calls the runner body directly: the hold makes every public entry point refuse."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def run_validation(
        self,
        snapshot: object,
        patch_diff: str,
        command: ValidationCommand,
        timeout_seconds: float | None = None,
        *,
        provenance: object | None = None,
    ) -> ValidationExecutionResult:
        result: ValidationExecutionResult = await self.inner._run_validation_unchecked(
            snapshot, patch_diff, command
        )
        return result


def _case(command: ValidationCommand, patch: str) -> tuple[FixProposal, FailureContext]:
    category, error = {
        ValidationCommand.PYTEST: (FailureCategory.TEST, "AssertionError"),
        ValidationCommand.RUFF: (FailureCategory.LINT, "F401"),
        ValidationCommand.MYPY: (FailureCategory.LINT, "mypy type error"),
        ValidationCommand.PYTHON_SYNTAX: (FailureCategory.BUILD, "SyntaxError"),
    }[command]
    context = FailureContext(
        incident_id="inc_exec",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=1,
        workflow_name="CI",
        commit_sha="a" * 40,
        branch="main",
        run_url="https://ci",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(category=category, error_type=error, message="x"),
    )
    proposal = FixProposal(
        proposal_id="fix_inc_exec",
        incident_id="inc_exec",
        diagnosis_id="diag",
        commit_sha="a" * 40,
        status="proposed",
        is_valid=True,
        unified_diff=patch,
        target_files=["pkg/calc.py"],
        rationale="r",
        assumptions=[],
        risk_level="low",
        has_dependency_changes=False,
        confidence_score=0.9,
    )
    return proposal, context


def _executor(tmp_path: Path, *, image: str = VALIDATOR, timeout: float = 120) -> Any:
    work = tmp_path / "exec-work"
    work.mkdir(mode=0o700, exist_ok=True)
    config = ExecutorConfig(
        socket_path=str(tmp_path / "unused.sock"),
        allowed_uids=str(os.getuid()),
        request_key=REQ,
        response_key=RESP,
        work_dir=str(work),
        image=image,
        timeout_seconds=timeout,
    )
    return create_app(config, gate=lambda: None)  # the hold is injected away for this test only


def _remote(app: Any) -> RemoteSandboxRunner:
    return RemoteSandboxRunner(
        url="unix:///unused.sock",
        request_key=REQ,
        response_key=RESP,
        transport=httpx.ASGITransport(app=app),
    )


async def _validate(runner: Any, files: dict[str, bytes], command: Any, patch: str) -> Any:
    proposal, context = _case(command, patch)
    service = ValidationService(
        sandbox_runner=_Unchecked(runner), snapshot_service=_Snapshots(files)
    )
    return await service._validate_fix_unchecked(proposal, context, None)


def _comparable(result: ValidationResult) -> dict[str, Any]:
    data = result.model_dump(exclude={"duration_ms", "created_at"})  # wall-clock fields
    for name in ("stdout", "stderr"):
        data[name] = re.sub(r"\d+\.\d+s", "<t>s", data[name])  # tool timing lines
    return data


@pytest.mark.asyncio
@pytest.mark.parametrize("command", list(ValidationCommand))
async def test_every_command_matches_local_validation_through_the_executor(
    tmp_path: Path, command: ValidationCommand
) -> None:
    local = await _validate(
        DockerSandboxRunner(image_name=VALIDATOR, timeout_seconds=120), PROJECT, command, PATCH
    )
    remote = await _validate(_remote(_executor(tmp_path)), PROJECT, command, PATCH)
    assert remote.status.value == "passed", remote.stdout + remote.stderr
    assert _comparable(remote) == _comparable(local)
    if command is ValidationCommand.PYTEST:
        assert "5 passed" in remote.stdout  # includes the four sandbox-restriction probes


@pytest.mark.asyncio
async def test_rejected_patch_matches_local_validation(tmp_path: Path) -> None:
    local = await _validate(
        DockerSandboxRunner(image_name=VALIDATOR), PROJECT, ValidationCommand.PYTEST, BAD_PATCH
    )
    remote = await _validate(
        _remote(_executor(tmp_path)), PROJECT, ValidationCommand.PYTEST, BAD_PATCH
    )
    assert remote.status.value == "patch_rejected"
    assert _comparable(remote) == _comparable(local)


@pytest.mark.asyncio
async def test_missing_image_matches_local_validation(tmp_path: Path) -> None:
    image = "akesis-does-not-exist:v0"
    local = await _validate(
        DockerSandboxRunner(image_name=image), PROJECT, ValidationCommand.PYTHON_SYNTAX, PATCH
    )
    remote = await _validate(
        _remote(_executor(tmp_path, image=image)), PROJECT, ValidationCommand.PYTHON_SYNTAX, PATCH
    )
    assert remote.status.value == "failed" and remote.exit_code == 125
    assert _comparable(remote) == _comparable(local)


@pytest.mark.asyncio
async def test_timeout_matches_local_and_executor_containers_are_labelled_and_removed(
    tmp_path: Path,
) -> None:
    local = await _validate(
        DockerSandboxRunner(image_name=VALIDATOR, timeout_seconds=8.0),
        SLOW,
        ValidationCommand.PYTEST,
        PATCH,
    )
    seen: list[list[str]] = []

    async def watch() -> None:
        while not seen or not seen[-1]:
            seen.append(await asyncio.to_thread(_labelled))
            await asyncio.sleep(0.5)

    watcher = asyncio.create_task(watch())
    remote = await _validate(
        _remote(_executor(tmp_path, timeout=8.0)), SLOW, ValidationCommand.PYTEST, PATCH
    )
    watcher.cancel()
    assert remote.status.value == "timed_out" and remote.timed_out
    assert _comparable(remote) == _comparable(local)
    assert any(seen), "the executor's container did not carry the executor label"
    await asyncio.sleep(1)
    assert _labelled() == []


def test_startup_cleanup_removes_only_executor_containers(tmp_path: Path) -> None:
    def start(label: str) -> str:
        return subprocess.run(
            ["docker", "run", "-d", "--rm", "--label", label, VALIDATOR, "sleep", "60"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    ours, other = start(EXECUTOR_LABEL), start("akesis.unrelated=1")
    try:
        work = tmp_path / "work"
        work.mkdir(mode=0o700)
        (work / "stale").mkdir()
        assert startup_cleanup(work) == 1
        running = subprocess.run(
            ["docker", "ps", "-q", "--no-trunc"], capture_output=True, text=True, check=False
        ).stdout.split()
        assert ours not in running and other in running
        assert list(work.iterdir()) == []
    finally:
        subprocess.run(["docker", "rm", "-f", ours, other], capture_output=True, check=False)


# --- the real entrypoint -------------------------------------------------------------------------


def _executor_env(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    sock = run / "exec.sock"
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "PYTHONPATH": str(REPO),
        "AKESIS_EXECUTOR_SOCKET_PATH": str(sock),
        "AKESIS_EXECUTOR_ALLOWED_UIDS": str(os.getuid()),
        "AKESIS_EXECUTOR_REQUEST_KEY": REQ,
        "AKESIS_EXECUTOR_RESPONSE_KEY": RESP,
        "AKESIS_EXECUTOR_WORK_DIR": str(tmp_path / "work"),
    }
    return env, cwd, sock


def _start(env: dict[str, str], cwd: Path, *extra: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, *extra, "-m", "src.apps.executor"],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.mark.asyncio
async def test_real_executor_process_returns_a_signed_held_rejection(tmp_path: Path) -> None:
    env, cwd, sock = _executor_env(tmp_path)
    process = _start(env, cwd)
    try:
        deadline = time.monotonic() + 30
        while True:  # the socket file exists from bind(); wait until uvicorn is listening
            assert process.poll() is None, process.communicate()[1]
            assert time.monotonic() < deadline
            try:
                with socket.socket(socket.AF_UNIX) as probe:  # accepted: this UID is allowlisted
                    probe.connect(str(sock))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                await asyncio.sleep(0.1)
        assert stat.S_IMODE(os.lstat(sock).st_mode) == 0o660

        runner = RemoteSandboxRunner(url=f"unix://{sock}", request_key=REQ, response_key=RESP)
        async with _Snapshots(PROJECT).prepare(None) as snapshot:
            with pytest.raises(ExecutorRejectedError) as refused:
                await runner._run_validation_unchecked(snapshot, PATCH, ValidationCommand.PYTEST)
        assert refused.value.code == "held"  # the unconditional hold, signed by the executor

        transport = httpx.AsyncHTTPTransport(uds=str(sock))
        async with httpx.AsyncClient(transport=transport, base_url="http://e") as client:
            health = await client.get("/healthz")
        assert (health.status_code, health.text) == (200, '{"status":"ok"}')
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.parametrize("unsafe", ["dotenv", "database_url", "credential"])
def test_real_executor_refuses_credentials_before_importing_settings(
    tmp_path: Path, unsafe: str
) -> None:
    env, cwd, sock = _executor_env(tmp_path)
    if unsafe == "dotenv":
        (cwd / ".env").write_text("GITHUB_APP_PRIVATE_KEY=synthetic\n")
    elif unsafe == "database_url":
        env["DATABASE_URL"] = "postgresql+asyncpg://u:p@127.0.0.1:1/x"
    else:
        env["SLACK_SIGNING_SECRET"] = "synthetic-secret-value"
    process = _start(env, cwd, "-X", "importtime")
    _, stderr = process.communicate(timeout=30)
    assert process.returncode == 2
    assert "refusing to start" in stderr or "credentials are set" in stderr
    assert "synthetic" not in stderr  # values are never echoed
    assert "src.packages.shared.config" not in stderr  # refused before Settings was imported
    assert not sock.exists()


# --- Phase 1.14b: a genuinely different kernel UID (no root needed) ---------------------------

_SUBUID_CLIENT = """
import socket, sys
c = socket.socket(socket.AF_UNIX)
try:
    c.connect(sys.argv[1])
except OSError as error:
    print("CONNECT_DENIED", error.errno)
    raise SystemExit
c.settimeout(5)
try:
    c.sendall(b"GET /healthz HTTP/1.1\\r\\nHost: e\\r\\nConnection: close\\r\\n\\r\\n")
    data = c.recv(4096)
except OSError:
    data = b""
print("RESPONSE " + data.split(b"\\r\\n")[0].decode() if data else "CLOSED")
"""


def _subordinate_uid() -> int | None:
    """Host UID that namespace UID 1 maps to under `unshare --map-auto`, if the host allows it."""
    user = subprocess.run(["id", "-un"], capture_output=True, text=True, check=False).stdout.strip()
    try:
        line = next(
            entry
            for entry in Path("/etc/subuid").read_text().splitlines()
            if entry.split(":")[0] == user
        )
    except (OSError, StopIteration):
        return None
    probe = subprocess.run(
        [
            "unshare",
            "--user",
            "--map-auto",
            "--setuid",
            "1",
            "--setgid",
            "1",
            "/usr/bin/python3",
            "-c",
            "print(1)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return int(line.split(":")[1]) + 1 if probe.returncode == 0 else None


def _client(sock: Path, *, as_other_uid: bool) -> str:
    prefix = ["unshare", "--user", "--map-auto", "--setuid", "1", "--setgid", "1"]
    command = [
        *(prefix if as_other_uid else []),
        "/usr/bin/python3",
        "-c",
        _SUBUID_CLIENT,
        str(sock),
    ]
    return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False).stdout


@pytest.mark.asyncio
async def test_a_different_kernel_uid_is_refused_by_permissions_and_by_so_peercred(
    tmp_path: Path,
) -> None:
    other_uid = _subordinate_uid()
    if other_uid is None:
        pytest.skip("host provides no subordinate UID range or unprivileged user namespaces")
    # The other UID must be able to traverse to the socket, so it lives in a fresh 0755 directory
    # under /tmp rather than in the 0700 pytest tree. The executor requires it to be owned by us
    # and not writable by anyone else.
    base = Path(tempfile.mkdtemp(prefix="akesis-uid-", dir="/tmp"))
    try:
        os.chmod(base, 0o755)
        env, cwd, _ = _executor_env(tmp_path)
        sock = base / "exec.sock"
        env["AKESIS_EXECUTOR_SOCKET_PATH"] = str(sock)
        process = _start(env, cwd)
        try:
            deadline = time.monotonic() + 30
            while "RESPONSE" not in _client(sock, as_other_uid=False):
                assert process.poll() is None, process.communicate()[1]
                assert time.monotonic() < deadline
                await asyncio.sleep(0.2)

            # Layer 1, production mode 0660: the other UID cannot even connect.
            assert stat.S_IMODE(os.lstat(sock).st_mode) == 0o660
            assert _client(sock, as_other_uid=True).split() == ["CONNECT_DENIED", "13"]

            # Layer 2: with the file permission opened (test only), SO_PEERCRED still refuses it.
            os.chmod(sock, 0o666)
            assert _client(sock, as_other_uid=True).strip() == "CLOSED"
            assert _client(sock, as_other_uid=False).startswith("RESPONSE HTTP/1.1 200")
        finally:
            process.terminate()
            out, _ = process.communicate(timeout=10)
        assert "executor_peer_refused" in out and str(other_uid) in out
    finally:
        shutil.rmtree(base, ignore_errors=True)
