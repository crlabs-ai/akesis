"""Phase 1.14b: the API never runs Docker in production.

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


def test_production_settings_require_the_executor() -> None:
    values = {k: v for k, v in _valid_production().items() if k not in EXECUTOR}
    with pytest.raises(ValueError, match="SANDBOX_EXECUTOR_URL is required in production"):
        Settings.model_validate(values)
    assert Settings.model_validate({**values, **EXECUTOR}).sandbox_executor_url


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "environment", "production")
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


def _production_env(tmp_path: Path, *, executor: bool) -> dict[str, str]:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "PYTHONPATH": str(REPO),
        "ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql+asyncpg://prod_user:Synthetic9@prod-db.internal:5432/akesis",
        "GITHUB_WEBHOOK_SECRET": "prod_sec_abc1234567890",
        "GITHUB_APP_ID": "12345",
        "GITHUB_APP_PRIVATE_KEY": "synthetic-app-key-material",
        "GEMINI_API_KEY": "AIzaSyProdKey1234567890",
        "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T01/B02/synthetic",
        "SLACK_SIGNING_SECRET": "prod_slack_sec_1234567890",
        "SLACK_APPROVER_USER_IDS": '["U_TEST"]',
        "SLACK_WORKSPACE_ID": "T_TEST",
        "SLACK_CHANNEL_ID": "C_TEST",
        "CORS_ALLOWED_ORIGINS": '["https://dashboard.akesis.dev"]',
        "TRUSTED_HOSTS": '["api.akesis.dev"]',
        "READ_API_TOKEN": "prod_read_token_distinct_987654",
    }
    if executor:
        env.update({name.upper(): value for name, value in EXECUTOR.items()})
    return env


# Any subprocess at all while the API module is imported and built fails the process.
_API_PROBE = """
import asyncio, subprocess
def refuse(*a, **k):
    raise SystemExit("subprocess started during API startup: " + repr(a))
subprocess.run = subprocess.Popen = refuse
asyncio.create_subprocess_exec = refuse
from src.apps.api import main, routes
o = routes.orchestrator
print(type(o.validation_service.sandbox_runner).__name__,
      type(o.mutation_service.validation_service.sandbox_runner).__name__)
"""


@pytest.mark.parametrize("executor", [False, True])
def test_real_api_startup_in_production(tmp_path: Path, executor: bool) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()  # no .env here: only the synthetic environment below is read
    result = subprocess.run(
        [sys.executable, "-c", _API_PROBE],
        cwd=cwd,
        env=_production_env(tmp_path, executor=executor),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if executor:
        assert result.returncode == 0, result.stderr
        assert result.stdout.split() == ["RemoteSandboxRunner", "RemoteSandboxRunner"]
    else:
        assert result.returncode != 0
        assert "SANDBOX_EXECUTOR_URL is required in production" in result.stderr


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


def test_documented_units_give_docker_only_to_the_executor() -> None:
    units = _units()
    api = next(body for cmd, body in units.items() if "uvicorn src.apps.api" in cmd)
    worker = next(body for cmd, body in units.items() if "-m src.apps.worker" in cmd)
    executor = next(body for cmd, body in units.items() if "-m src.apps.executor" in cmd)
    for body in (api, worker):
        assert "docker" not in body.lower(), body
    assert re.search(r"^SupplementaryGroups=akesis-exec$", api, flags=re.M)
    assert re.search(r"^User=akesis-exec$", executor, flags=re.M)
    assert re.search(r"^SupplementaryGroups=docker$", executor, flags=re.M)
    assert re.search(r"^Requires=docker.service$", executor, flags=re.M)


def test_no_doc_adds_the_application_user_to_the_docker_group() -> None:
    offenders = [
        str(path.relative_to(REPO))
        for path in (REPO / "docs").rglob("*.md")
        if re.search(r"usermod -aG docker akesis(?!-exec)", path.read_text())
    ]
    assert offenders == []
