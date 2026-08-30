import subprocess
from pathlib import Path

import pytest

from src.packages.sdk.sandbox_runner import DockerSandboxRunner
from src.packages.shared.models import ValidationCommand


def is_docker_functional() -> bool:
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, timeout=3.0)
        return res.returncode == 0
    except Exception:
        return False


@pytest.mark.docker
@pytest.mark.skipif(not is_docker_functional(), reason="Docker daemon is not available")
@pytest.mark.asyncio
async def test_real_docker_sandbox_execution(tmp_path: Path) -> None:
    source_repo = tmp_path / "source_repo"
    source_repo.mkdir()
    (source_repo / "main.py").write_text("def compute():\n    return 42\n", encoding="utf-8")

    patch = (
        "--- a/main.py\n"
        "+++ b/main.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def compute():\n"
        "+    # validated in docker\n"
        "     return 42\n"
    )

    # Use standard python:3.12-slim if custom validator is not yet built on host
    runner = DockerSandboxRunner(image_name="python:3.12-slim")
    result = await runner.run_validation(
        repo_source_dir=source_repo,
        patch_diff=patch,
        command=ValidationCommand.PYTHON_SYNTAX,
    )

    assert result.exit_code == 0
    assert result.timed_out is False
