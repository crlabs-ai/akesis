import io
import zipfile

import pytest
import respx

from src.packages.sdk.github_client import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubClient,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubResourceNotFoundError,
)


@pytest.mark.asyncio
async def test_get_workflow_run_success() -> None:
    client = GitHubClient(token="mock_token", base_url="https://api.github.com")

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/123").respond(
            status_code=200,
            json={"id": 123, "status": "completed", "conclusion": "failure"},
        )
        result = await client.get_workflow_run("crlabs-ai", "akesis", 123)
        assert result["id"] == 123
        assert result["conclusion"] == "failure"


@pytest.mark.asyncio
async def test_get_workflow_run_64bit_id() -> None:
    run_id_64 = 33539502900
    client = GitHubClient(token="mock_token", base_url="https://api.github.com")

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get(f"/repos/crlabs-ai/akesis/actions/runs/{run_id_64}").respond(
            status_code=200,
            json={"id": run_id_64, "status": "completed", "conclusion": "failure"},
        )
        result = await client.get_workflow_run("crlabs-ai", "akesis", run_id_64)
        assert result["id"] == run_id_64


@pytest.mark.asyncio
async def test_get_workflow_run_auth_failure_401() -> None:
    client = GitHubClient(token="invalid_token", base_url="https://api.github.com")

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/123").respond(
            status_code=401,
            json={"message": "Bad credentials"},
        )
        with pytest.raises(GitHubAuthError) as exc:
            await client.get_workflow_run("crlabs-ai", "akesis", 123)
        assert "Unauthorized (HTTP 401)" in str(exc.value)


@pytest.mark.asyncio
async def test_get_workflow_run_permission_denied_403() -> None:
    client = GitHubClient(token="mock_token", base_url="https://api.github.com")

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/123").respond(
            status_code=403,
            text="Resource not accessible by integration (permission denied)",
        )
        with pytest.raises(GitHubPermissionError) as exc:
            await client.get_workflow_run("crlabs-ai", "akesis", 123)
        assert "Permission denied (HTTP 403)" in str(exc.value)


@pytest.mark.asyncio
async def test_get_workflow_run_not_found() -> None:
    client = GitHubClient(token="mock_token", base_url="https://api.github.com")

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/999").respond(
            status_code=404,
            json={"message": "Not Found"},
        )
        with pytest.raises(GitHubResourceNotFoundError):
            await client.get_workflow_run("crlabs-ai", "akesis", 999)


@pytest.mark.asyncio
async def test_get_workflow_run_rate_limit() -> None:
    client = GitHubClient(token="mock_token", base_url="https://api.github.com")

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/123").respond(
            status_code=429,
            json={"message": "Rate limit exceeded"},
        )
        with pytest.raises(GitHubRateLimitError):
            await client.get_workflow_run("crlabs-ai", "akesis", 123)


@pytest.mark.asyncio
async def test_get_workflow_run_server_error() -> None:
    client = GitHubClient(token="mock_token", base_url="https://api.github.com")

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/123").respond(
            status_code=500,
            json={"message": "Internal Server Error"},
        )
        with pytest.raises(GitHubAPIError):
            await client.get_workflow_run("crlabs-ai", "akesis", 123)


@pytest.mark.asyncio
async def test_get_workflow_run_logs_success() -> None:
    client = GitHubClient(token="mock_token", base_url="https://api.github.com")

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/123/logs").respond(
            status_code=200,
            text="Raw log data here",
        )
        logs = await client.get_workflow_run_logs("crlabs-ai", "akesis", 123)
        assert logs == "Raw log data here"


@pytest.mark.asyncio
async def test_get_workflow_run_logs_not_found() -> None:
    client = GitHubClient(token="mock_token", base_url="https://api.github.com")

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/999/logs").respond(
            status_code=404,
            text="Not Found",
        )
        with pytest.raises(GitHubResourceNotFoundError):
            await client.get_workflow_run_logs("crlabs-ai", "akesis", 999)


@pytest.mark.asyncio
async def test_get_workflow_run_logs_zip_archive() -> None:
    client = GitHubClient(token="mock_token", base_url="https://api.github.com")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        z.writestr("job/1_step.txt", "Step 1 output\n")
        z.writestr("job/2_test.txt", "pytest failed with error\n")
    zip_bytes = zip_buffer.getvalue()

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/123/logs").respond(
            status_code=200,
            content=zip_bytes,
            headers={"Content-Type": "application/zip"},
        )
        logs = await client.get_workflow_run_logs("crlabs-ai", "akesis", 123)
        assert "Step 1 output" in logs
        assert "pytest failed with error" in logs
        assert "=== job/1_step.txt ===" in logs
        assert "=== job/2_test.txt ===" in logs


@pytest.mark.asyncio
async def test_get_workflow_run_logs_zip_archive_ignores_directories_and_non_txt() -> None:
    client = GitHubClient(token="mock_token", base_url="https://api.github.com")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        z.writestr("job/", "")  # Directory entry
        z.writestr("job/summary.json", '{"status": "ok"}')  # Non-.txt file
        z.writestr("job/0_setup.txt", "Setup complete\n")
        z.writestr("job/1_build.txt", "Build succeeded\n")
    zip_bytes = zip_buffer.getvalue()

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/123/logs").respond(
            status_code=200,
            content=zip_bytes,
            headers={"Content-Type": "application/zip"},
        )
        logs = await client.get_workflow_run_logs("crlabs-ai", "akesis", 123)
        assert "=== job/0_setup.txt ===" in logs
        assert "=== job/1_build.txt ===" in logs
        assert "summary.json" not in logs
        assert "=== job/ ===" not in logs
        # Check alphabetical sorting
        setup_idx = logs.index("=== job/0_setup.txt ===")
        build_idx = logs.index("=== job/1_build.txt ===")
        assert setup_idx < build_idx


@pytest.mark.asyncio
async def test_get_workflow_run_logs_corrupted_zip_archive_raises_github_api_error() -> None:
    client = GitHubClient(token="mock_token", base_url="https://api.github.com")

    corrupted_bytes = b"PK\x03\x04corrupted_header_not_valid_zip"

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/123/logs").respond(
            status_code=200,
            content=corrupted_bytes,
            headers={"Content-Type": "application/zip"},
        )
        with pytest.raises(GitHubAPIError) as exc_info:
            await client.get_workflow_run_logs("crlabs-ai", "akesis", 123)
        assert "corrupted logs archive" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_get_commit_success_and_not_found() -> None:
    client = GitHubClient(token="mock_token", base_url="https://api.github.com")

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/commits/abcdef123456").respond(
            status_code=200,
            json={"sha": "abcdef123456", "commit": {"message": "fix: bug"}},
        )
        respx_mock.get("/repos/crlabs-ai/akesis/commits/missing").respond(
            status_code=404,
            json={"message": "Not Found"},
        )

        commit = await client.get_commit("crlabs-ai", "akesis", "abcdef123456")
        assert commit["sha"] == "abcdef123456"

        with pytest.raises(GitHubResourceNotFoundError):
            await client.get_commit("crlabs-ai", "akesis", "missing")


@pytest.mark.asyncio
async def test_create_pull_request_success() -> None:
    client = GitHubClient(token="mock_token", base_url="https://api.github.com")

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.post("/repos/crlabs-ai/akesis/pulls").respond(
            status_code=201,
            json={"number": 10, "html_url": "https://github.com/crlabs-ai/akesis/pull/10"},
        )
        res = await client.create_pull_request(
            owner="crlabs-ai",
            repo="akesis",
            title="fix title",
            body="fix body",
            head="akesis/fix/1",
            base="main",
        )
        assert res["number"] == 10


@pytest.mark.asyncio
async def test_find_pull_request_found_and_empty() -> None:
    client = GitHubClient(token="mock_token", base_url="https://api.github.com")

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/pulls").respond(
            status_code=200,
            json=[{"number": 12, "html_url": "https://github.com/crlabs-ai/akesis/pull/12"}],
        )
        res = await client.find_pull_request(
            owner="crlabs-ai",
            repo="akesis",
            head_branch="akesis/fix/1",
            base_branch="main",
        )
        assert res is not None
        assert res["number"] == 12

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/pulls").respond(
            status_code=200,
            json=[],
        )
        res_empty = await client.find_pull_request(
            owner="crlabs-ai",
            repo="akesis",
            head_branch="akesis/fix/none",
            base_branch="main",
        )
        assert res_empty is None
