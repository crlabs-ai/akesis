import pytest
import respx

from src.packages.sdk.github_client import (
    GitHubAPIError,
    GitHubClient,
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
async def test_get_workflow_run_logs_rate_limit() -> None:
    client = GitHubClient(token="mock_token", base_url="https://api.github.com")

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/123/logs").respond(
            status_code=403,
            text="Forbidden",
        )
        with pytest.raises(GitHubRateLimitError):
            await client.get_workflow_run_logs("crlabs-ai", "akesis", 123)


@pytest.mark.asyncio
async def test_get_workflow_run_logs_server_error() -> None:
    client = GitHubClient(token="mock_token", base_url="https://api.github.com")

    with respx.mock(base_url="https://api.github.com") as respx_mock:
        respx_mock.get("/repos/crlabs-ai/akesis/actions/runs/123/logs").respond(
            status_code=502,
            text="Bad Gateway",
        )
        with pytest.raises(GitHubAPIError):
            await client.get_workflow_run_logs("crlabs-ai", "akesis", 123)
