"""Phases 1.14b and 1.14c: the API never runs Docker or calls the executor; the worker does.

Covers production configuration, the runner's own production refusal, which runner every service
selects, the real API module starting (or refusing) in a subprocess, and the documented systemd
units.
"""

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from src.packages.sdk.remote_sandbox_runner import RemoteSandboxRunner, default_sandbox_runner
from src.packages.sdk.sandbox_runner import DockerSandboxRunner, SandboxError
from src.packages.shared import config as config_module
from src.packages.shared.config import Settings
from tests.unit.test_read_api_security import _valid_production

REPO = Path(__file__).resolve().parents[2]
EXECUTOR = {
    "sandbox_executor_url": "unix:///run/akesis-exec/exec.sock",
    "sandbox_executor_request_key": "q" * 40,
    "sandbox_executor_response_key": "r" * 40,
}


def test_production_settings_assign_the_executor_to_the_worker_only() -> None:
    api = _valid_production()
    Settings.model_validate(api)  # the API needs no executor configuration
    with pytest.raises(ValueError, match="SANDBOX_EXECUTOR_\\* must not be set for the API"):
        Settings.model_validate({**api, **EXECUTOR})
    with pytest.raises(ValueError, match="SANDBOX_EXECUTOR_\\* must not be set for the API"):
        Settings.model_validate({**api, "sandbox_executor_request_key": "q" * 40})

    worker = {
        "environment": "production",
        "service_role": "worker",
        "database_url": api["database_url"],
        "github_app_id": 12345,
        "github_app_private_key": "synthetic-app-key-material",
        "slack_approver_user_ids": ["U_TEST"],
        # Explicit, so the result does not depend on a developer .env in the working directory.
        "gemini_api_key": "",
        "slack_webhook_url": "",
        "github_webhook_secret": "",
        "slack_signing_secret": "",
        "github_app_client_secret": "",
        "auth_token_encryption_key": "",
    }
    with pytest.raises(ValueError, match="SANDBOX_EXECUTOR_URL is required in production"):
        Settings.model_validate(worker)
    # The worker needs no webhook secret, Slack signing secret, sign-in secrets, CORS or hosts.
    assert Settings.model_validate({**worker, **EXECUTOR}).service_role == "worker"
    # ...and refuses them: it verifies no inbound request and serves no read API.
    for name in (
        "github_webhook_secret",
        "slack_signing_secret",
        "github_app_client_secret",
        "auth_token_encryption_key",
    ):
        with pytest.raises(ValueError, match=f"must not be set for the worker: {name.upper()}"):
            Settings.model_validate({**worker, **EXECUTOR, name: api[name]})


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "environment", "production")
    monkeypatch.setattr(config_module.settings, "service_role", "worker")
    for name, value in EXECUTOR.items():
        monkeypatch.setattr(config_module.settings, name, value)


@pytest.fixture
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    started: list[Any] = []

    def refuse(*args: Any, **kwargs: Any) -> Any:
        started.append(args)
        raise AssertionError(f"subprocess started: {args!r}")

    async def refuse_async(*args: Any, **kwargs: Any) -> Any:
        started.append(args)
        raise AssertionError(f"subprocess started: {args!r}")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse_async)
    return started


def test_local_docker_runner_cannot_be_built_in_production(production: None) -> None:
    with pytest.raises(SandboxError, match="disabled in production"):
        DockerSandboxRunner()
    assert DockerSandboxRunner(executor=True).executor  # the executor process only


@pytest.mark.asyncio
async def test_a_runner_built_earlier_still_cannot_run_docker_in_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_subprocess: list[Any]
) -> None:
    runner = DockerSandboxRunner()  # built outside production
    monkeypatch.setattr(config_module.settings, "environment", "production")
    with pytest.raises(SandboxError):
        await runner._run_in_workspace(tmp_path, "", ["pytest", "-v"], 5)
    with pytest.raises(SandboxError):
        await runner._execute_container(tmp_path, ["pytest", "-v"], 5)
    assert no_subprocess == []


def test_every_production_service_uses_only_the_executor(production: None) -> None:
    from src.packages.shared.mutation_service import GitMutationService
    from src.packages.shared.remediation_orchestrator import RemediationOrchestrator
    from src.packages.shared.validation_service import ValidationService

    orchestrator = RemediationOrchestrator()
    runners = [
        default_sandbox_runner(),
        ValidationService().sandbox_runner,
        GitMutationService().validation_service.sandbox_runner,
        getattr(orchestrator.validation_service, "sandbox_runner", None),
        orchestrator.mutation_service.validation_service.sandbox_runner,  # type: ignore[attr-defined]
    ]
    assert all(isinstance(runner, RemoteSandboxRunner) for runner in runners)


_API_ENV = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": "postgresql+asyncpg://prod_user:Synthetic9@prod-db.internal:5432/akesis",
    "GITHUB_WEBHOOK_SECRET": "prod_sec_abc1234567890",
    "GITHUB_APP_ID": "12345",
    "GITHUB_APP_PRIVATE_KEY": "synthetic-app-key-material",
    "SLACK_SIGNING_SECRET": "prod_slack_sec_1234567890",
    "SLACK_APPROVER_USER_IDS": '["U_TEST"]',
    "SLACK_WORKSPACE_ID": "T_TEST",
    "SLACK_CHANNEL_ID": "C_TEST",
    "CORS_ALLOWED_ORIGINS": '["https://dashboard.akesis.dev"]',
    "TRUSTED_HOSTS": '["api.akesis.dev"]',
    "GITHUB_APP_CLIENT_ID": "Iv1.synthetic0000",
    "GITHUB_APP_CLIENT_SECRET": "synthetic-client-secret-0123456789",
    "AUTH_REDIRECT_URI": "https://dashboard.akesis.dev/auth/callback",
    "AUTH_TOKEN_ENCRYPTION_KEY": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=",
}
_WORKER_ENV = {
    "ENVIRONMENT": "production",
    "SERVICE_ROLE": "worker",
    "DATABASE_URL": _API_ENV["DATABASE_URL"],
    "GITHUB_APP_ID": "12345",
    "GITHUB_APP_PRIVATE_KEY": "synthetic-app-key-material",
    "SLACK_APPROVER_USER_IDS": '["U_TEST"]',
    **{name.upper(): value for name, value in EXECUTOR.items()},
}

# Any subprocess at all while the service module is imported and built fails the process.
_NO_SUBPROCESS = """
import asyncio, subprocess
def refuse(*a, **k):
    raise SystemExit("subprocess started during startup: " + repr(a))
subprocess.run = subprocess.Popen = refuse
asyncio.create_subprocess_exec = refuse
"""
_API_PROBE = (
    _NO_SUBPROCESS
    + """
import sys
from src.apps.api import main, routes
from src.packages.shared.config import settings
loaded = [m for m in sys.modules if m.endswith(("remediation_orchestrator", "sandbox_runner"))]
print("orchestrator" if hasattr(routes, "orchestrator") else "no-orchestrator", loaded,
      bool(settings.sandbox_executor_request_key or settings.sandbox_executor_response_key))
"""
)
_WORKER_PROBE = (
    _NO_SUBPROCESS
    + """
from src.apps.worker import __main__ as worker
from src.packages.shared.remediation_orchestrator import RemediationOrchestrator
worker.require_worker_role()
config = worker.build_config()
o = RemediationOrchestrator()
print(config.approval_execution is not None,
      type(o.mutation_service.validation_service.sandbox_runner).__name__)
"""
)


def _run(tmp_path: Path, probe: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)  # no .env here: only the synthetic environment below is read
    base = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PYTHONPATH": str(REPO)}
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=cwd,
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_real_api_startup_holds_no_executor_and_no_orchestrator(tmp_path: Path) -> None:
    result = _run(tmp_path, _API_PROBE, _API_ENV)
    assert result.returncode == 0, result.stderr
    # The API never imports the orchestrator or any sandbox runner, and holds no executor key.
    assert result.stdout.split() == ["no-orchestrator", "[]", "False"]


@pytest.mark.parametrize(
    "extra, message",
    [
        ({"SANDBOX_EXECUTOR_REQUEST_KEY": "q" * 40}, "must not be set for the API"),
        ({"SANDBOX_EXECUTOR_URL": "unix:///run/akesis-exec/exec.sock"}, "Sandbox executor"),
        # Worker-role settings with API secrets are refused by the worker check itself.
        (
            {"SERVICE_ROLE": "worker", **{n.upper(): v for n, v in EXECUTOR.items()}},
            "must not be set for the worker",
        ),
    ],
)
def test_real_api_refuses_executor_configuration(
    tmp_path: Path, extra: dict[str, str], message: str
) -> None:
    result = _run(tmp_path, _API_PROBE, {**_API_ENV, **extra})
    assert result.returncode != 0 and message in result.stderr


def test_real_api_refuses_a_valid_worker_configuration(tmp_path: Path) -> None:
    # Valid worker settings (executor keys, no API secrets) must still never start the API.
    result = _run(tmp_path, _API_PROBE, _WORKER_ENV)
    assert result.returncode != 0 and "SERVICE_ROLE=api" in result.stderr


def test_real_worker_startup_uses_only_the_executor(tmp_path: Path) -> None:
    result = _run(tmp_path, _WORKER_PROBE, _WORKER_ENV)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["True", "RemoteSandboxRunner"]


@pytest.mark.parametrize(
    "env, message",
    [
        ({k: v for k, v in _WORKER_ENV.items() if k != "SANDBOX_EXECUTOR_URL"}, "required in"),
        ({k: v for k, v in _WORKER_ENV.items() if k != "SERVICE_ROLE"}, "must not be set"),
        *(
            (
                {**_WORKER_ENV, name: _API_ENV[name]},
                f"API-only credentials must not be set for the worker: {name}",
            )
            for name in (
                "GITHUB_WEBHOOK_SECRET",
                "SLACK_SIGNING_SECRET",
                "GITHUB_APP_CLIENT_SECRET",
                "AUTH_TOKEN_ENCRYPTION_KEY",
            )
        ),
    ],
)
def test_real_worker_refuses_without_executor_or_worker_role_or_with_api_secrets(
    tmp_path: Path, env: dict[str, str], message: str
) -> None:
    result = _run(tmp_path, _WORKER_PROBE, env)
    assert result.returncode != 0 and message in result.stderr


@pytest.mark.parametrize(
    "name, value",
    [
        ("DATABASE_URL", _API_ENV["DATABASE_URL"]),  # both application roles
        ("GITHUB_APP_PRIVATE_KEY", _API_ENV["GITHUB_APP_PRIVATE_KEY"]),
        ("GITHUB_WEBHOOK_SECRET", _API_ENV["GITHUB_WEBHOOK_SECRET"]),  # API only
        ("SLACK_SIGNING_SECRET", _API_ENV["SLACK_SIGNING_SECRET"]),
        ("GITHUB_APP_CLIENT_SECRET", _API_ENV["GITHUB_APP_CLIENT_SECRET"]),
        ("AUTH_TOKEN_ENCRYPTION_KEY", _API_ENV["AUTH_TOKEN_ENCRYPTION_KEY"]),
        ("SANDBOX_EXECUTOR_REQUEST_KEY", "q" * 40),  # the worker's caller-side key
    ],
)
def test_real_executor_refuses_application_credentials(
    tmp_path: Path, name: str, value: str
) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PYTHONPATH": str(REPO), name: value}
    result = subprocess.run(
        [sys.executable, "-m", "src.apps.executor"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 2, result.stderr
    assert "Application credentials are set" in result.stderr and name in result.stderr
    assert value not in result.stderr  # names only, never values


def _units() -> dict[str, str]:
    text = (REPO / "docs/deployment/README.md").read_text()
    units: dict[str, str] = {}
    for block in re.findall(r"```ini\n(.*?)```", text, flags=re.S):
        start = re.search(r"^ExecStart=(.*)$", block, flags=re.M)
        if start:  # directives only: comments may mention what a unit must not do
            units[start.group(1)] = "\n".join(
                line for line in block.splitlines() if not line.lstrip().startswith("#")
            )
    return units


def test_documented_units_separate_api_worker_and_executor_identities() -> None:
    units = _units()
    api = next(body for cmd, body in units.items() if "uvicorn src.apps.api" in cmd)
    worker = next(body for cmd, body in units.items() if "-m src.apps.worker" in cmd)
    executor = next(body for cmd, body in units.items() if "-m src.apps.executor" in cmd)
    # The API: its own user, no Docker, no executor socket group.
    assert re.search(r"^User=akesis$", api, flags=re.M)
    assert "docker" not in api.lower() and "akesis-exec" not in api
    assert "SupplementaryGroups" not in api
    # The worker: its own user, the executor socket group only, no Docker.
    assert re.search(r"^User=akesis-worker$", worker, flags=re.M)
    assert re.search(r"^SupplementaryGroups=akesis-exec$", worker, flags=re.M)
    assert "docker" not in worker.lower()
    # Separate working directories, so each loads only its own .env (no shared credentials).
    api_dir = re.search(r"^WorkingDirectory=(.*)$", api, flags=re.M)
    worker_dir = re.search(r"^WorkingDirectory=(.*)$", worker, flags=re.M)
    assert api_dir and worker_dir and api_dir.group(1) != worker_dir.group(1)
    assert "EnvironmentFile" not in api and "EnvironmentFile" not in worker
    assert re.search(r"^Environment=SERVICE_ROLE=worker$", worker, flags=re.M)
    # The executor alone has Docker.
    assert re.search(r"^User=akesis-exec$", executor, flags=re.M)
    assert re.search(r"^SupplementaryGroups=docker$", executor, flags=re.M)
    assert re.search(r"^Requires=docker.service$", executor, flags=re.M)


def test_no_doc_gives_the_api_user_docker_or_executor_access() -> None:
    offenders = [
        str(path.relative_to(REPO))
        for path in (REPO / "docs").rglob("*.md")
        if re.search(r"usermod -aG (docker|akesis-exec) akesis(?![-\w])", path.read_text())
    ]
    assert offenders == []
